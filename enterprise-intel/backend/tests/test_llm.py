import pytest
from pydantic import BaseModel
from sqlalchemy import text

from app.config import settings
from app.llm.base import CallContext, LLMError, LLMUnavailable
from app.llm.gateway import db_recorder
from app.rag.schemas import Citation, RAGAnswer
from app.rag.sources import Source
from app.rag.verifier import deterministic_checks, verify_answer
from tests.fakes import ScriptedProvider, gateway_for, rate_limited


class Thing(BaseModel):
    value: int


def records_of(gateway):
    seen = []
    gateway.recorder = seen.append
    return seen


def test_fallback_when_primary_is_rate_limited():
    primary = ScriptedProvider("primary", [rate_limited()])
    backup = ScriptedProvider("backup", [{"value": 7}])
    gw = gateway_for(primary, backup, max_retries=1)
    seen = records_of(gw)

    result = gw.generate("answer", "sys", [{"role": "user", "content": "hi"}], Thing)

    assert result.parsed.value == 7 and result.provider == "backup" and result.fallback_used is True
    assert len(primary.calls) == 2  # first try plus one retry, then fall back
    assert [r.success for r in seen] == [False, False, True]
    assert seen[-1].fallback_used is True


def test_retry_succeeds_without_fallback():
    primary = ScriptedProvider("primary", [rate_limited(), rate_limited(), {"value": 1}])
    gw = gateway_for(primary, ScriptedProvider("backup", [{"value": 2}]))
    result = gw.generate("answer", "sys", [{"role": "user", "content": "hi"}], Thing)
    assert result.provider == "primary" and result.fallback_used is False and result.parsed.value == 1


def test_auth_error_skips_retries():
    primary = ScriptedProvider("primary", [LLMError("HTTP 401", auth=True)])
    gw = gateway_for(primary, ScriptedProvider("backup", [{"value": 2}]))
    result = gw.generate("answer", "sys", [{"role": "user", "content": "hi"}], Thing)
    assert len(primary.calls) == 1 and result.provider == "backup"


def test_malformed_json_gets_one_repair_retry():
    primary = ScriptedProvider("primary", ["not json at all", {"value": 3}])
    gw = gateway_for(primary)
    result = gw.generate("answer", "sys", [{"role": "user", "content": "hi"}], Thing)
    assert result.parsed.value == 3 and len(primary.calls) == 2
    repair = primary.calls[1][-1]["content"]
    assert "failed validation" in repair


def test_invalid_output_twice_falls_back_to_next_provider():
    primary = ScriptedProvider("primary", [{"value": "abc"}])
    backup = ScriptedProvider("backup", [{"value": 9}])
    result = gateway_for(primary, backup).generate("answer", "s", [{"role": "user", "content": "q"}], Thing)
    assert result.provider == "backup" and len(primary.calls) == 2


def test_json_in_code_fence_is_accepted():
    gw = gateway_for(ScriptedProvider("p", ['```json\n{"value": 5}\n```']))
    assert gw.generate("answer", "s", [{"role": "user", "content": "q"}], Thing).parsed.value == 5


def test_circuit_breaker_skips_a_dead_provider(monkeypatch):
    monkeypatch.setattr(settings, "circuit_threshold", 2)
    primary = ScriptedProvider("primary", [LLMError("HTTP 500", retryable=True)])
    gw = gateway_for(primary, ScriptedProvider("backup", [{"value": 1}]), max_retries=0)
    for _ in range(4):
        gw.generate("answer", "s", [{"role": "user", "content": "q"}], Thing)
    assert len(primary.calls) == 2  # circuit opened after 2 failures; later requests skip it entirely


def test_all_providers_failing_raises_unavailable():
    gw = gateway_for(ScriptedProvider("a", [LLMError("boom")]), ScriptedProvider("b", [LLMError("bam")]))
    with pytest.raises(LLMUnavailable):
        gw.generate("answer", "s", [{"role": "user", "content": "q"}], Thing)


def test_preferred_model_goes_first_and_usage_accumulates():
    a, b = ScriptedProvider("a", [{"value": 1}]), ScriptedProvider("b", [{"value": 2}])
    gw = gateway_for(a, b)
    ctx = CallContext(preferred_model="b-model")
    result = gw.generate("answer", "s", [{"role": "user", "content": "q"}], Thing, ctx)
    assert result.provider == "b" and ctx.calls == 1 and ctx.input_tokens == 10 and "b-model" in ctx.models


def test_calls_are_logged_to_llm_calls_table(client):
    from app.db.session import engine

    gw = gateway_for(ScriptedProvider("p", [{"value": 1}]))
    gw.recorder = db_recorder
    gw.generate("answer", "s", [{"role": "user", "content": "q"}], Thing)
    with engine.connect() as conn:
        row = conn.execute(text("SELECT provider, task, success, input_tokens FROM llm_calls ORDER BY created_at DESC LIMIT 1")).one()
    assert tuple(row) == ("p", "answer", True, 10)


# --- verifier -------------------------------------------------------------------------------------------------------
def source(sid="S1", content="The database ran out of disk space on Monday."):
    import uuid

    return Source(sid, uuid.uuid4(), uuid.uuid4(), "a.md", content, 1, "Incidents")


def test_deterministic_checks_drop_bad_citations():
    answer = RAGAnswer(
        answer="Disk full [S1]. Also [S9].",
        citations=[Citation(source_id="S1", quote="ran out of disk space"),
                   Citation(source_id="S1", quote="totally invented quote about network"),
                   Citation(source_id="S5", quote="x")],
    )  # fmt: skip
    cleaned, issues = deterministic_checks(answer, [source()])
    assert [c.quote for c in cleaned.citations] == ["ran out of disk space"]
    assert any("S9" in i for i in issues) and any("not found" in i for i in issues)


def test_uncited_answer_is_flagged():
    _, issues = deterministic_checks(RAGAnswer(answer="Just trust me."), [source()])
    assert "answer has no citations" in issues


def test_low_groundedness_triggers_one_regeneration():
    judge = ScriptedProvider("j", [{"claims": [{"claim": "c", "verdict": "no"}]}, {"claims": [{"claim": "c", "verdict": "yes"}]}])
    good = RAGAnswer(answer="Disk full [S1]", citations=[Citation(source_id="S1", quote="ran out of disk space")])
    calls = []

    def regenerate():
        calls.append(1)
        return good

    first = RAGAnswer(answer="Made up [S1]", citations=[Citation(source_id="S1", quote="ran out of disk space")])
    result = verify_answer(gateway_for(judge), first, [source()], CallContext(), regenerate)
    assert calls == [1] and result.regenerated and result.groundedness == 1.0 and result.answer is good


def test_still_unsupported_after_retry_is_flagged_partially_supported():
    judge = ScriptedProvider("j", [{"claims": [{"claim": "c", "verdict": "no"}]}])
    answer = RAGAnswer(answer="Made up [S1]", citations=[Citation(source_id="S1", quote="ran out of disk space")])
    result = verify_answer(gateway_for(judge), answer, [source()], CallContext(), lambda: answer)
    assert any("partially supported" in i for i in result.issues)


def test_claude_provider_falls_back_to_auto_tool_choice_when_forced_is_rejected():
    import json

    import httpx

    from app.llm.anthropic import ClaudeProvider

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body["tool_choice"]["type"])
        if body["tool_choice"]["type"] == "tool":
            return httpx.Response(400, json={"error": {"message": 'tool_choice: type "tool" is not supported'}})
        return httpx.Response(200, json={
            "content": [{"type": "tool_use", "name": "respond", "input": {"value": 4}}],
            "usage": {"input_tokens": 5, "output_tokens": 2}, "stop_reason": "tool_use",
        })  # fmt: skip

    provider = ClaudeProvider("key")
    provider._client = httpx.Client(base_url="https://api.anthropic.com", transport=httpx.MockTransport(handler))
    msgs = [{"role": "user", "content": "q"}]
    assert provider.generate("sonnet-x", "sys", msgs, Thing, 100, 5).json == {"value": 4}
    assert provider.generate("sonnet-x", "sys", msgs, Thing, 100, 5).json == {"value": 4}
    assert seen == ["tool", "auto", "auto"]  # learned once, then goes straight to auto


def test_report_format_argument_is_lenient():
    from app.agent.tools.report import ReportArgs

    assert ReportArgs(title="Report", format="Markdown").format == "markdown"
    assert ReportArgs(title="Report", format="text").format == "markdown"
    assert ReportArgs(title="Report", format="JSON").format == "json"
