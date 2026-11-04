import logging
import time
import uuid
from collections.abc import Callable

from sqlalchemy import select

from app.agent.graph import Runtime, build_graph, initial_state
from app.config import settings
from app.db.models import AgentStep, Conversation, Document, Message
from app.db.session import SessionLocal
from app.llm.base import CallContext
from app.llm.gateway import get_gateway
from app.logging_utils import log_activity
from app.memory.manager import load_memory, maybe_summarize, update_after_answer
from app.observability import get_langfuse

logger = logging.getLogger("app.chat")
ERROR_TEXT = "Sorry, something went wrong while answering. Please try again."


def run_chat_turn(
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    content: str,
    document_ids: list[uuid.UUID],
    model: str | None,
    emit: Callable[[str, dict], None],
) -> None:
    """One chat turn: save the question, run the graph, save the answer, update memory. Streams events via emit."""
    gateway = get_gateway()
    langfuse = get_langfuse()
    started = time.perf_counter()
    started_ns = time.time_ns()
    trace_id = uuid.uuid4().hex

    with SessionLocal() as db:
        conv = db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user_id))
        if conv is None:
            emit("error", {"message": "Conversation not found"})
            return
        memory = load_memory(db, conv)  # loaded before the new message is saved
        db.add(Message(conversation_id=conv.id, user_id=user_id, role="user", content=content))
        if conv.title is None:
            conv.title = content[:60]
        db.commit()
        # A UI document scope only counts for documents the user actually owns.
        scope = list(db.scalars(
            select(Document.id).where(Document.id.in_(document_ids), Document.user_id == user_id)
        )) if document_ids else []  # fmt: skip

    ctx = CallContext(user_id=user_id, conversation_id=conversation_id, trace_id=trace_id, preferred_model=model)
    rt = Runtime(gateway, ctx, emit, deadline=time.monotonic() + settings.agent_timeout_s)

    try:
        state = build_graph(rt).invoke(
            initial_state(user_id, conversation_id, content, memory, scope), {"recursion_limit": 60}
        )
        final = state["final_answer"]
        status, text, route = final.status, final.answer, state["route"]
    except Exception:
        logger.exception("chat turn failed conversation=%s", conversation_id)
        state, final, status, text, route = None, None, "error", ERROR_TEXT, None

    sources = {s.id: s for s in state["sources"]} if state else {}
    quotes = {c.source_id: c.quote for c in final.citations} if final else {}
    cited_ids = list(dict.fromkeys(c.source_id for c in final.citations)) if final else []
    citations = [sources[i].as_citation(quotes.get(i, "")) for i in cited_ids if i in sources]
    latency_ms = round((time.perf_counter() - started) * 1000)
    details = {
        "models": sorted(ctx.models), "input_tokens": ctx.input_tokens, "output_tokens": ctx.output_tokens,
        "cost_usd": round(ctx.cost_usd, 6), "latency_ms": latency_ms, "llm_calls": ctx.calls,
        "fallback_used": ctx.fallback_used, "issues": state["issues"] if state else [],
        "conflicts": [c.model_dump() for c in final.conflicts] if final else [],
        "confidence": final.confidence if final else None,
        "flagged_sources": [s.id for s in sources.values() if s.flagged],
    }  # fmt: skip

    with SessionLocal() as db:
        conv = db.get(Conversation, conversation_id)
        message = Message(
            conversation_id=conversation_id, user_id=user_id, role="assistant", content=text, citations=citations,
            route=route, status=status, groundedness_score=state["groundedness"] if state else None, details=details,
        )  # fmt: skip
        db.add(message)
        db.flush()
        for step in (state["steps"] if state else []):
            db.add(AgentStep(message_id=message.id, user_id=user_id, step_no=step["step_no"], tool_name=step["tool_name"],
                             args=step["args"], output_summary=step["output_summary"], status=step["status"],
                             error=step["error"], latency_ms=step["latency_ms"]))  # fmt: skip
        db.commit()
        ctx.message_id = message.id
        if state:
            update_after_answer(db, conv, [sources[i].document_id for i in cited_ids if i in sources])
            maybe_summarize(db, gateway, conv, ctx)
        log_activity(db, user_id, "chat", "conversation", conversation_id,
                     {"route": route, "status": status, "latency_ms": latency_ms}, "error" if status == "error" else "info")  # fmt: skip
        message_id = message.id

    if status == "error":
        emit("error", {"message": text})
    emit("citations", {"citations": citations, "conflicts": details["conflicts"]})
    for i in range(0, len(text), 60):  # the answer is complete by now; chunked so the UI can render progressively
        emit("token", {"text": text[i : i + 60]})
    emit("done", {"message_id": str(message_id), "route": route, "status": status,
                  "groundedness": details and (state["groundedness"] if state else None), **{
                      k: details[k] for k in ("models", "input_tokens", "output_tokens", "cost_usd", "latency_ms",
                                              "fallback_used", "issues")}})  # fmt: skip
    for step in state["steps"] if state else []:
        langfuse.tool(trace_id=trace_id, name=step["tool_name"], input=step["args"],
                      output=step["output_summary"], latency_ms=step["latency_ms"], user_id=user_id,
                      session_id=conversation_id, error=step["error"])  # fmt: skip
    langfuse.root(trace_id, "chat_turn", user_id, conversation_id, started_ns, content, text,
                  {"route": route, "status": status, "models": details["models"], "latency_ms": latency_ms,
                   "groundedness": state["groundedness"] if state else None},
                  error=text if status == "error" else None)  # fmt: skip
