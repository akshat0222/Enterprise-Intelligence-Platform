import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.db.models import ActivityLog, Chunk, Conversation, Document, LLMCall, Message, User
from app.db.session import get_db

router = APIRouter(tags=["stats"])


class ActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str | None
    entity_type: str | None
    entity_id: uuid.UUID | None
    details: dict | None
    level: str
    created_at: datetime


class LLMCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task: str | None
    provider: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    latency_ms: int | None
    success: bool | None
    error: str | None
    fallback_used: bool
    created_at: datetime


def _count(db: Session, model, user_id) -> int:
    return db.scalar(select(func.count()).select_from(model).where(model.user_id == user_id)) or 0


@router.get("/stats/overview")
def overview(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    calls = db.execute(
        select(
            func.count(), func.coalesce(func.sum(LLMCall.input_tokens + LLMCall.output_tokens), 0),
            func.coalesce(func.sum(LLMCall.cost_usd), 0), func.coalesce(func.avg(LLMCall.latency_ms), 0),
            func.count().filter(LLMCall.success.is_(False)), func.count().filter(LLMCall.fallback_used.is_(True)),
        ).where(LLMCall.user_id == user.id)
    ).one()  # fmt: skip
    total, tokens, cost, avg_latency, errors, fallbacks = calls
    return {
        "documents": _count(db, Document, user.id),
        "chunks": _count(db, Chunk, user.id),
        "conversations": _count(db, Conversation, user.id),
        "messages": _count(db, Message, user.id),
        "llm_calls": total,
        "tokens": int(tokens),
        "cost_usd": float(cost),
        "avg_latency_ms": round(float(avg_latency)),
        "error_rate": round(errors / total, 4) if total else 0.0,
        "fallback_calls": fallbacks,
    }


@router.get("/stats/usage")
def usage(range: str = Query("7d", pattern=r"^\d{1,3}d$"), user: User = Depends(get_current_user),
          db: Session = Depends(get_db)):  # fmt: skip
    days = int(range[:-1])
    rows = db.execute(
        text("""
            SELECT date_trunc('day', created_at)::date AS day, provider,
                   COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens, COUNT(*) AS calls
            FROM llm_calls
            WHERE user_id = :uid AND created_at >= now() - make_interval(days => :days)
            GROUP BY 1, 2 ORDER BY 1
        """),
        {"uid": user.id, "days": days},
    ).all()
    return {"range": range, "series": [{"day": str(r.day), "provider": r.provider, "tokens": int(r.tokens),
                                        "calls": r.calls} for r in rows]}  # fmt: skip


@router.get("/logs/activity", response_model=list[ActivityOut])
def activity_log(
    level: str | None = None, limit: int = Query(50, ge=1, le=200), before_id: int | None = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):  # fmt: skip
    query = select(ActivityLog).where(ActivityLog.user_id == user.id)
    if level:
        query = query.where(ActivityLog.level == level)
    if before_id:
        query = query.where(ActivityLog.id < before_id)
    return db.scalars(query.order_by(ActivityLog.id.desc()).limit(limit)).all()


@router.get("/logs/llm-calls", response_model=list[LLMCallOut])
def llm_call_log(
    errors_only: bool = False, model: str | None = None, limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0), user: User = Depends(get_current_user), db: Session = Depends(get_db),
):  # fmt: skip
    query = select(LLMCall).where(LLMCall.user_id == user.id)
    if errors_only:
        query = query.where(LLMCall.success.is_(False))
    if model:
        query = query.where(LLMCall.model == model)
    return db.scalars(query.order_by(LLMCall.created_at.desc()).limit(limit).offset(offset)).all()
