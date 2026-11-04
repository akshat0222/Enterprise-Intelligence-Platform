import json
import queue
import threading
import uuid
from collections.abc import Iterator
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.chat.service import run_chat_turn
from app.db.models import AgentStep, Conversation, Message, User
from app.db.session import get_db
from app.llm.gateway import get_gateway
from app.ratelimit import limiter

router = APIRouter(tags=["conversations"])


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    active_document_ids: list[uuid.UUID]
    created_at: datetime
    updated_at: datetime


class StepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    step_no: int | None
    tool_name: str | None
    args: dict | None
    output_summary: dict | None
    status: str | None
    error: str | None
    latency_ms: int | None


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    citations: list | None
    route: str | None
    status: str | None
    groundedness_score: float | None
    details: dict | None
    created_at: datetime
    steps: list[StepOut] = []


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class CreateConversation(BaseModel):
    title: str | None = Field(None, max_length=200)


class RenameConversation(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class SendMessage(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    document_ids: list[uuid.UUID] = []
    model: str | None = None


def owned_conversation(db: Session, user: User, conversation_id: uuid.UUID) -> Conversation:
    conv = db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user.id))
    if conv is None:
        raise HTTPException(404, "Conversation not found")
    return conv


@router.get("/models")
def list_models(user: User = Depends(get_current_user)):
    return {"models": get_gateway().available_models()}


@router.post("/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(body: CreateConversation, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conv = Conversation(user_id=user.id, title=body.title)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = select(Conversation).where(Conversation.user_id == user.id)
    return db.scalars(query.order_by(Conversation.updated_at.desc()).limit(limit).offset(offset)).all()


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conv = owned_conversation(db, user, conversation_id)
    messages = db.scalars(
        select(Message).where(Message.conversation_id == conv.id, Message.user_id == user.id).order_by(Message.created_at)
    ).all()
    steps = db.scalars(
        select(AgentStep).where(AgentStep.user_id == user.id, AgentStep.message_id.in_([m.id for m in messages]))
        .order_by(AgentStep.step_no)
    ).all()  # fmt: skip
    by_message: dict[uuid.UUID, list[AgentStep]] = {}
    for s in steps:
        by_message.setdefault(s.message_id, []).append(s)
    out = [
        MessageOut(
            id=m.id, role=m.role, content=m.content, citations=m.citations, route=m.route, status=m.status,
            groundedness_score=m.groundedness_score, details=m.details, created_at=m.created_at,
            steps=[StepOut.model_validate(s) for s in by_message.get(m.id, [])],
        )
        for m in messages
    ]  # fmt: skip
    return ConversationDetail(**ConversationOut.model_validate(conv).model_dump(), messages=out)


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
def rename_conversation(
    conversation_id: uuid.UUID, body: RenameConversation, user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):  # fmt: skip
    conv = owned_conversation(db, user, conversation_id)
    conv.title = body.title
    db.commit()
    return conv


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.delete(owned_conversation(db, user, conversation_id))  # messages and steps cascade
    db.commit()


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.post("/conversations/{conversation_id}/messages")
@limiter.limit("30/minute")
def send_message(
    request: Request,
    conversation_id: uuid.UUID,
    body: SendMessage,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owned_conversation(db, user, conversation_id)  # 404 before any streaming starts
    events: queue.Queue = queue.Queue()
    finished = object()

    def worker() -> None:
        try:
            run_chat_turn(
                user_id=user.id, conversation_id=conversation_id, content=body.content,
                document_ids=body.document_ids, model=body.model, emit=lambda e, d: events.put((e, d)),
            )  # fmt: skip
        except Exception:
            events.put(("error", {"message": "Internal error"}))
        finally:
            events.put(finished)

    threading.Thread(target=worker, daemon=True).start()

    def stream() -> Iterator[str]:
        while (item := events.get()) is not finished:
            yield _sse(*item)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
