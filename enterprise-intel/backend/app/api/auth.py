import uuid
from datetime import datetime

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.auth.security import (
    DUMMY_HASH,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.config import settings
from app.db.models import User
from app.db.session import get_db
from app.logging_utils import log_activity
from app.ratelimit import limiter

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: str
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


def _set_refresh_cookie(response: Response, user_id: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        create_refresh_token(user_id),
        max_age=settings.refresh_token_days * 86400,
        httponly=True,
        secure=settings.is_production,
        samesite="none" if settings.is_production else "lax",
        path="/auth",
    )


def _token_response(user: User, response: Response) -> TokenOut:
    _set_refresh_cookie(response, str(user.id))
    return TokenOut(access_token=create_access_token(str(user.id)), user=UserOut.model_validate(user))


@router.post("/register", response_model=UserOut, status_code=201)
@limiter.limit("10/minute")
def register(request: Request, body: Credentials, db: Session = Depends(get_db)):
    email = body.email.lower()
    user = User(email=email, password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "An account with this email already exists") from None
    log_activity(db, user.id, "register", "user", user.id)
    return user


@router.post("/login", response_model=TokenOut)
@limiter.limit("10/minute")
def login(request: Request, response: Response, body: Credentials, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    # Always run one bcrypt comparison so response time doesn't reveal whether the email exists.
    ok = verify_password(body.password, user.password_hash if user else DUMMY_HASH)
    if user is None or not ok:
        log_activity(db, user.id if user else None, "login_failed", level="warning")
        raise HTTPException(401, "Invalid email or password")
    log_activity(db, user.id, "login", "user", user.id)
    return _token_response(user, response)


@router.post("/refresh", response_model=TokenOut)
@limiter.limit("60/minute")
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise HTTPException(401, "No refresh token")
    try:
        user = db.get(User, uuid.UUID(decode_token(token, "refresh")))
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(401, "Invalid or expired refresh token") from None
    if user is None:
        raise HTTPException(401, "Invalid or expired refresh token")
    return _token_response(user, response)


@router.post("/logout", status_code=204)
def logout(response: Response):
    response.delete_cookie(REFRESH_COOKIE, path="/auth")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
