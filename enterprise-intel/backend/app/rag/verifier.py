import re
from collections.abc import Callable
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from app.config import settings
from app.llm.base import CallContext, LLMUnavailable
from app.llm.gateway import LLMGateway
from app.prompts import JUDGE_SYSTEM_V1
from app.rag.schemas import Citation, Judgement, RAGAnswer
from app.rag.sources import Source

MARKER = re.compile(r"\[(S\d+)\]")
QUOTE_MATCH_THRESHOLD = 80
VERDICT_SCORE = {"yes": 1.0, "partial": 0.5, "no": 0.0}


@dataclass
class Verification:
    answer: RAGAnswer
    groundedness: float | None = None
    issues: list[str] = field(default_factory=list)
    regenerated: bool = False


def deterministic_checks(answer: RAGAnswer, sources: list[Source]) -> tuple[RAGAnswer, list[str]]:
    """Citation markers must point at provided sources, quotes must really appear in them."""
    by_id = {s.id: s for s in sources}
    issues: list[str] = []

    unknown = {m for m in MARKER.findall(answer.answer) if m not in by_id}
    if unknown:
        issues.append(f"answer cites unknown sources: {', '.join(sorted(unknown))}")

    valid: list[Citation] = []
    for c in answer.citations:
        source = by_id.get(c.source_id)
        if source is None:
            issues.append(f"citation {c.source_id} is not a provided source")
        elif c.quote and fuzz.partial_ratio(c.quote.lower(), source.content.lower()) < QUOTE_MATCH_THRESHOLD:
            issues.append(f"quote for {c.source_id} not found in the source")
        else:
            valid.append(c)

    if answer.status != "insufficient_evidence" and not valid and not MARKER.search(answer.answer):
        issues.append("answer has no citations")
    answer.citations = valid
    return answer, issues


def judge(gateway: LLMGateway, answer: RAGAnswer, sources: list[Source], ctx: CallContext) -> float | None:
    """LLM-as-judge: fraction of claims supported by the sources (yes=1, partial=0.5, no=0)."""
    from app.rag.sources import format_sources

    prompt = f"<sources>\n{format_sources(sources, max_chars=1500)}\n</sources>\n\n<answer>\n{answer.answer[:6000]}\n</answer>"
    try:
        result = gateway.generate("judge", JUDGE_SYSTEM_V1, [{"role": "user", "content": prompt}], Judgement, ctx)
    except LLMUnavailable:
        return None
    claims = result.parsed.claims
    if not claims:
        return None
    return round(sum(VERDICT_SCORE[c.verdict] for c in claims) / len(claims), 3)


def verify_answer(
    gateway: LLMGateway,
    answer: RAGAnswer,
    sources: list[Source],
    ctx: CallContext,
    regenerate: Callable[[], RAGAnswer] | None = None,
) -> Verification:
    if answer.status == "insufficient_evidence" or not sources:
        return Verification(answer)

    answer, issues = deterministic_checks(answer, sources)
    score = judge(gateway, answer, sources, ctx)
    result = Verification(answer, score, issues)

    if score is not None and score < settings.groundedness_threshold:
        if regenerate is not None:
            retry, retry_issues = deterministic_checks(regenerate(), sources)
            retry_score = judge(gateway, retry, sources, ctx)
            result.regenerated = True
            if retry_score is not None and retry_score >= score:
                result.answer, result.groundedness, result.issues = retry, retry_score, retry_issues
        if result.groundedness is not None and result.groundedness < settings.groundedness_threshold:
            result.issues.append(f"partially supported (groundedness {result.groundedness})")
    return result
