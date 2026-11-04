import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Conversation, Message
from app.llm.base import CallContext, LLMUnavailable
from app.llm.gateway import LLMGateway
from app.prompts import REWRITE_SYSTEM_V1, SUMMARY_SYSTEM_V1
from app.rag.schemas import Rewritten

RECENT_MESSAGES = 6
MAX_MESSAGE_CHARS = 600
SUMMARIZE_ABOVE = 10  # messages in the conversation before older turns are folded into the summary


@dataclass
class Memory:
    summary: str | None = None
    recent: list[dict] = field(default_factory=list)  # {"role", "content"}, oldest first
    active_document_ids: list[uuid.UUID] = field(default_factory=list)

    def as_text(self) -> str:
        """What the LLM sees: rolling summary plus the last few turns, trimmed. Never raw retrieved chunks."""
        parts = []
        if self.summary:
            parts.append(f"Summary of earlier conversation: {self.summary}")
        parts += [f"{m['role']}: {m['content']}" for m in self.recent]
        return "\n".join(parts)


def load_memory(db: Session, conversation: Conversation) -> Memory:
    rows = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id, Message.user_id == conversation.user_id)
        .order_by(Message.created_at.desc())
        .limit(RECENT_MESSAGES)
    ).all()
    recent = [{"role": m.role, "content": m.content[:MAX_MESSAGE_CHARS]} for m in reversed(rows)]
    return Memory(conversation.summary, recent, list(conversation.active_document_ids or []))


def rewrite_query(gateway: LLMGateway, question: str, memory: Memory, ctx: CallContext) -> Rewritten:
    """Turn a follow-up into a standalone query. First messages need no rewrite (and no LLM call)."""
    if not memory.recent and not memory.summary:
        return Rewritten(query=question)
    prompt = f"<conversation>\n{memory.as_text()}\n</conversation>\n\nLatest message: {question}"
    try:
        return gateway.generate("rewrite", REWRITE_SYSTEM_V1, [{"role": "user", "content": prompt}], Rewritten, ctx).parsed
    except LLMUnavailable:
        return Rewritten(query=question)


def update_after_answer(db: Session, conversation: Conversation, cited_document_ids: list[uuid.UUID]) -> None:
    if cited_document_ids:
        conversation.active_document_ids = list(dict.fromkeys(cited_document_ids))
    conversation.updated_at = func.now()
    db.commit()


def maybe_summarize(db: Session, gateway: LLMGateway, conversation: Conversation, ctx: CallContext) -> None:
    """Fold turns older than the verbatim window into conversations.summary (cheap model)."""
    total = db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == conversation.id))
    if total <= SUMMARIZE_ABOVE or total % 4:
        return
    older = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id, Message.user_id == conversation.user_id)
        .order_by(Message.created_at)
        .limit(total - RECENT_MESSAGES)
    ).all()
    transcript = "\n".join(f"{m.role}: {m.content[:400]}" for m in older)[-6000:]
    prompt = f"Previous summary: {conversation.summary or '(none)'}\n\nConversation so far:\n{transcript}"
    try:
        result = gateway.generate("summary", SUMMARY_SYSTEM_V1, [{"role": "user", "content": prompt}], None, ctx, 400)
    except LLMUnavailable:
        return
    conversation.summary = result.text.strip()
    db.commit()
