import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

import app.agent.tools  # noqa: F401  (registers the tools)
from app.agent.registry import ToolArgsError, ToolContext, ToolError, describe_tools, run_tool
from app.agent.schemas import AnalysisResult, NextAction, Report, render_analysis
from app.config import settings
from app.db.session import SessionLocal
from app.llm.base import CallContext, LLMUnavailable
from app.llm.gateway import LLMGateway
from app.memory.manager import Memory, rewrite_query
from app.prompts import DIRECT_SYSTEM_V1, PLANNER_SYSTEM_V1, ROUTER_SYSTEM_V1
from app.rag.answer import answer_from_sources, insufficient_answer
from app.rag.schemas import Citation, RAGAnswer, RouterDecision
from app.rag.sources import Source, from_hit
from app.rag.verifier import verify_answer
from app.retrieval.hybrid import hybrid_search

MAX_REPAIRS = 2


class AgentState(TypedDict, total=False):
    user_id: uuid.UUID  # from the JWT, never from the LLM
    conversation_id: uuid.UUID
    question: str
    rewritten_query: str | None
    route: Literal["direct", "rag", "agent"]
    doc_scope: list[uuid.UUID]
    memory: Memory
    steps: list[dict]
    sources: list[Source]
    analysis: AnalysisResult | None
    report: Report | None
    report_markdown: str | None
    errors: list[str]
    step_count: int
    repairs: int
    pending_action: dict | None
    insufficient: bool
    final_answer: RAGAnswer | None
    groundedness: float | None
    issues: list[str]
    seen_calls: list[str]


@dataclass
class Runtime:
    gateway: LLMGateway
    ctx: CallContext
    emit: Callable[[str, dict], None]
    deadline: float  # monotonic time after which the agent run is cut off


def initial_state(user_id, conversation_id, question, memory: Memory, doc_scope) -> AgentState:
    return AgentState(
        user_id=user_id, conversation_id=conversation_id, question=question, rewritten_query=None, memory=memory,
        doc_scope=doc_scope, steps=[], sources=[], analysis=None, report=None, report_markdown=None, errors=[],
        step_count=0, repairs=0, pending_action=None, insufficient=False, final_answer=None, groundedness=None,
        issues=[], seen_calls=[],
    )  # fmt: skip


def build_graph(rt: Runtime):
    gw = rt.gateway

    # --- entry ------------------------------------------------------------------------------------------------
    def router(state: AgentState) -> dict:
        prompt = f"<conversation>\n{state['memory'].as_text() or '(new conversation)'}\n</conversation>\n\nMessage: {state['question']}"
        try:
            decision = gw.generate(
                "router", ROUTER_SYSTEM_V1, [{"role": "user", "content": prompt}], RouterDecision, rt.ctx
            ).parsed
            route = decision.route
        except LLMUnavailable:
            route = "rag"  # safest default: grounded retrieval
        rt.emit("route", {"route": route})
        return {"route": route}

    # --- paths ------------------------------------------------------------------------------------------------
    def direct_answer(state: AgentState) -> dict:
        convo = [{"role": m["role"], "content": m["content"]} for m in state["memory"].recent]
        convo.append({"role": "user", "content": state["question"]})
        if convo[0]["role"] != "user":
            convo = convo[1:]
        text = gw.generate("direct", DIRECT_SYSTEM_V1, convo, None, rt.ctx, 500).text.strip()
        return {"final_answer": RAGAnswer(answer=text, status="answered", confidence="high")}

    def rag_answer(state: AgentState) -> dict:
        rewritten = rewrite_query(gw, state["question"], state["memory"], rt.ctx)
        scope = state["doc_scope"]
        if not scope and rewritten.refers_to_previous_documents:
            scope = state["memory"].active_document_ids  # "that document" -> the documents just discussed
        rt.emit("tool_start", {"step": 1, "tool": "hybrid_search", "args": {"query": rewritten.query}})
        t0 = time.perf_counter()
        with SessionLocal() as db:
            result = hybrid_search(db, state["user_id"], rewritten.query, scope or None, top_k=8)
        sources = [from_hit(h, f"S{i}") for i, h in enumerate(result.hits, start=1)]
        rt.emit("tool_end", {"step": 1, "tool": "hybrid_search", "status": "ok",
                             "latency_ms": round((time.perf_counter() - t0) * 1000),
                             "summary": {"found": len(sources), "insufficient_evidence": result.insufficient_evidence}})  # fmt: skip

        if result.insufficient_evidence:
            final = insufficient_answer(sources)  # skip the LLM entirely: no evidence, no generation
        else:
            final = answer_from_sources(gw, rewritten.query, sources, state["memory"].as_text(), rt.ctx)
        return {"rewritten_query": rewritten.query, "sources": sources, "final_answer": final,
                "insufficient": result.insufficient_evidence, "doc_scope": list(scope)}  # fmt: skip

    # --- agent loop ------------------------------------------------------------------------------------------
    def planner(state: AgentState) -> dict:
        if time.monotonic() > rt.deadline:
            return {"pending_action": None, "errors": [*state["errors"], "agent run timed out"]}
        steps = "\n".join(
            f"{s['step_no']}. {s['tool_name']}({json.dumps(s['args'])[:200]}) -> {s['status']}: "
            f"{json.dumps(s.get('output_summary') or s.get('error'))[:400]}"
            for s in state["steps"]
        ) or "(no steps yet)"
        prompt = (
            f"<conversation>\n{state['memory'].as_text() or '(new conversation)'}\n</conversation>\n\n"
            f"Task: {state['question']}\n\nTools:\n{describe_tools()}\n\nSteps so far:\n{steps}\n\n"
            "Choose the next tool, or \"finish\"."
        )
        try:
            action = gw.generate("plan", PLANNER_SYSTEM_V1, [{"role": "user", "content": prompt}], NextAction, rt.ctx).parsed
        except LLMUnavailable as exc:
            return {"pending_action": None, "errors": [*state["errors"], f"planner unavailable: {exc}"]}
        if action.tool == "finish":
            return {"pending_action": None}
        return {"pending_action": {"tool": action.tool, "args": action.args}}

    def execute_tool(state: AgentState) -> dict:
        action = state["pending_action"]
        tool, args = action["tool"], action["args"]
        step_no = state["step_count"] + 1
        key = f"{tool}:{json.dumps(args, sort_keys=True, default=str)}"

        # Loop detection: the same call twice ends the run instead of burning steps.
        if key in state["seen_calls"]:
            return {"pending_action": None, "errors": [*state["errors"], f"loop detected: repeated {tool}"],
                    "step_count": step_no}  # fmt: skip

        rt.emit("tool_start", {"step": step_no, "tool": tool, "args": args})
        t0 = time.perf_counter()
        tctx = ToolContext(
            state["user_id"], gw, rt.ctx,
            {"sources": state["sources"], "analysis": state["analysis"], "doc_scope": state["doc_scope"],
             "question": state["question"]},
        )  # fmt: skip
        update: dict[str, Any] = {}
        record = {"step_no": step_no, "tool_name": tool, "args": args, "output_summary": None, "error": None}
        repairs = state["repairs"]
        try:
            result = run_tool(tool, args, tctx)
            record.update(status="ok", output_summary=result.summary)
            update = dict(result.update)
            if result.insufficient:
                update["insufficient"] = True
        except ToolArgsError as exc:
            record.update(status="invalid_args", error=str(exc))
            repairs += 1
        except ToolError as exc:
            record.update(status="error", error=str(exc))
        record["latency_ms"] = round((time.perf_counter() - t0) * 1000)
        rt.emit("tool_end", {"step": step_no, "tool": tool, "status": record["status"],
                             "latency_ms": record["latency_ms"], "summary": record["output_summary"],
                             "error": record["error"]})  # fmt: skip

        errors = state["errors"] + ([record["error"]] if record["error"] else [])
        return {**update, "steps": [*state["steps"], record], "step_count": step_no, "errors": errors,
                "repairs": repairs, "pending_action": None, "seen_calls": [*state["seen_calls"], key]}  # fmt: skip

    def after_planner(state: AgentState) -> str:
        return "execute_tool" if state.get("pending_action") else "agent_finish"

    def after_tool(state: AgentState) -> str:
        done = (
            state.get("insufficient")
            or state.get("report") is not None
            or state["step_count"] >= settings.max_agent_steps
            or state["repairs"] > MAX_REPAIRS
            or time.monotonic() > rt.deadline
            or any(e.startswith("loop detected") for e in state["errors"])
        )
        return "agent_finish" if done else "planner"

    def agent_finish(state: AgentState) -> dict:
        sources = state["sources"]
        if state.get("insufficient") or not sources:
            return {"final_answer": insufficient_answer(sources)}
        if state.get("report_markdown"):
            body = state["report_markdown"]
        elif state.get("analysis") is not None:
            body = render_analysis(state["analysis"])  # partial results beat none
        else:
            return {"final_answer": insufficient_answer(sources)}
        used = [s for s in sources if f"[{s.id}]" in body]
        note = "\n\n_Note: this result is partial; some steps did not complete._" if state["errors"] and not state.get("report_markdown") else ""
        return {"final_answer": RAGAnswer(answer=body + note, citations=[Citation(source_id=s.id) for s in used],
                                          confidence="medium", status="answered")}  # fmt: skip

    # --- verification ---------------------------------------------------------------------------------------
    def verify(state: AgentState) -> dict:
        final = state["final_answer"]
        regenerate = None
        if state["route"] == "rag" and not state.get("insufficient"):
            regenerate = lambda: answer_from_sources(  # noqa: E731
                gw, state["rewritten_query"] or state["question"], state["sources"], state["memory"].as_text(),
                rt.ctx, strict=True,
            )  # fmt: skip
        result = verify_answer(gw, final, state["sources"], rt.ctx, regenerate)
        rt.emit("verification", {"groundedness": result.groundedness, "issues": result.issues,
                                 "regenerated": result.regenerated})  # fmt: skip
        return {"final_answer": result.answer, "groundedness": result.groundedness, "issues": result.issues}

    g = StateGraph(AgentState)
    for name, fn in [("router", router), ("direct_answer", direct_answer), ("rag_answer", rag_answer),
                     ("planner", planner), ("execute_tool", execute_tool), ("agent_finish", agent_finish),
                     ("verify", verify)]:  # fmt: skip
        g.add_node(name, fn)
    g.add_edge(START, "router")
    g.add_conditional_edges("router", lambda s: s["route"],
                            {"direct": "direct_answer", "rag": "rag_answer", "agent": "planner"})  # fmt: skip
    g.add_edge("direct_answer", END)
    g.add_edge("rag_answer", "verify")
    g.add_conditional_edges("planner", after_planner, {"execute_tool": "execute_tool", "agent_finish": "agent_finish"})
    g.add_conditional_edges("execute_tool", after_tool, {"planner": "planner", "agent_finish": "agent_finish"})
    g.add_edge("agent_finish", "verify")
    g.add_edge("verify", END)
    return g.compile()

