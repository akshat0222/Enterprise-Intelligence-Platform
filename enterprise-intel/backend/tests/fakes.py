import re

from app.llm.base import LLMError, RawResult
from app.llm.gateway import LLMGateway, Route

TASKS = ["router", "rewrite", "judge", "summary", "direct", "plan", "answer", "analysis", "report"]


def gateway_for(*providers, **kwargs) -> LLMGateway:
    """A gateway whose every task routes through the given fake providers, in order."""
    return LLMGateway(
        {p.name: p for p in providers},
        {t: [Route(p.name, f"{p.name}-model") for p in providers] for t in TASKS},
        sleep=lambda s: None,
        **kwargs,
    )


class ScriptedProvider:
    """Plays back a list of outcomes: an Exception is raised, a RawResult/dict/str is returned."""

    def __init__(self, name: str, outcomes: list):
        self.name = name
        self.outcomes = list(outcomes)
        self.calls: list[list[dict]] = []

    def generate(self, model, system, messages, schema, max_tokens, timeout):
        self.calls.append(messages)
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, RawResult):
            return outcome
        if isinstance(outcome, dict):
            return RawResult("", outcome, 10, 5, "end_turn")
        return RawResult(outcome, None, 10, 5, "end_turn")


def rate_limited() -> LLMError:
    return LLMError("HTTP 429", retryable=True)


class SmartFake:
    """Answers every structured request from the prompt itself, so chat and agent flows run end to end offline."""

    name = "fake"

    def __init__(self):
        self.schemas: list[str] = []
        self.prompts: list[tuple[str, str]] = []  # (schema name, last user message)
        self.judge_verdict = "yes"
        self.bad_args_first = False
        self.planner_mode = "chain"  # chain | repeat | wander
        self._planner_calls = 0

    def generate(self, model, system, messages, schema, max_tokens, timeout):
        name = schema.__name__ if schema else "text"
        user = messages[-1]["content"]
        self.schemas.append(name)
        self.prompts.append((name, user))
        data = getattr(self, f"_{name}", None)
        if name == "text":
            return RawResult("Hello! Ask me about your documents.", None, 8, 6, "end_turn")
        return RawResult("", data(user), 20, 10, "end_turn")

    @staticmethod
    def _first_source(user: str) -> tuple[str, str]:
        m = re.search(r'<source id="(S\d+)"[^>]*>\n(?:WARNING:[^\n]*\n)?(.*?)\n</source>', user, re.S)
        return (m.group(1), m.group(2)) if m else ("S1", "")

    def _RouterDecision(self, user):
        msg = re.search(r"Message: (.*)$", user, re.S).group(1).lower()
        if "report" in msg or "analy" in msg:
            return {"route": "agent", "reason": "analysis"}
        return {"route": "direct" if msg.startswith(("hello", "hi ")) else "rag", "reason": "test"}

    def _Rewritten(self, user):
        msg = re.search(r"Latest message: (.*)$", user, re.S)
        text = msg.group(1) if msg else user
        return {"query": text, "refers_to_previous_documents": " it " in f" {text.lower()} "}

    def _RAGAnswer(self, user):
        sid, text = self._first_source(user)
        quote = text.strip().splitlines()[0][:80] if text.strip() else ""
        return {
            "answer": f"{quote} [{sid}]",
            "citations": [{"source_id": sid, "quote": quote}],
            "confidence": "high",
            "status": "answered",
        }

    def _Judgement(self, user):
        return {"claims": [{"claim": "the answer", "verdict": self.judge_verdict}]}

    def _NextAction(self, user):
        self._planner_calls += 1
        task = re.search(r"Task: (.*?)\n", user).group(1)
        steps = user.split("Steps so far:")[1]
        if self.planner_mode == "repeat":
            return {"tool": "knowledge_base_search", "args": {"query": "database outage"}}
        if self.planner_mode == "wander":
            return {"tool": "knowledge_base_search", "args": {"query": f"database outage {self._planner_calls}"}}
        def succeeded(tool: str) -> bool:
            return re.search(rf"{tool}\(.*?\) -> ok", steps) is not None

        if self.bad_args_first and "no steps yet" in steps:
            return {"tool": "knowledge_base_search", "args": {"query": "x"}}
        if not succeeded("knowledge_base_search"):
            return {"tool": "knowledge_base_search", "args": {"query": "database outage root cause"}}
        if not succeeded("document_analysis"):
            return {"tool": "document_analysis", "args": {"analysis_type": "root_causes", "focus": task}}
        if not succeeded("generate_report"):
            return {"tool": "generate_report", "args": {"title": "System Failure Analysis"}}
        return {"tool": "finish", "args": {}}

    def _AnalysisResult(self, user):
        ids = re.findall(r'<source id="(S\d+)"', user)[:2]
        return {"findings": [{"point": "Disk space exhaustion", "evidence": ids, "frequency": len(ids)}],
                "conflicts": [], "gaps": ["No root cause for the payment timeout"]}  # fmt: skip

    def _Report(self, user):
        return {
            "title": "System Failure Analysis",
            "executive_summary": "Disk exhaustion is the main cause of failures.",
            "findings": [{"heading": "Disk space", "detail": "The database ran out of disk space.", "evidence": ["S1"]}],
            "recommendations": ["Add disk usage alerts"],
            "missing_info": ["Payment timeout root cause"],
        }
