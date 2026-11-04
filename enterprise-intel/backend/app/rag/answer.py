import re

from app.llm.base import CallContext
from app.llm.gateway import LLMGateway
from app.prompts import ANSWER_SYSTEM_V1, PROMPT_MARKER, STRICT_ADDENDUM
from app.rag.schemas import RAGAnswer
from app.rag.sources import Source, format_sources

URL = re.compile(r"https?://[^\s)>\]]+")
NOT_FOUND = "I couldn't find this in your documents."


def insufficient_answer(sources: list[Source]) -> RAGAnswer:
    closest = list(dict.fromkeys(s.filename for s in sources))[:3]
    hint = f"\n\nClosest documents: {', '.join(closest)}." if closest else ""
    return RAGAnswer(answer=NOT_FOUND + hint, status="insufficient_evidence", confidence="low")


def answer_from_sources(
    gateway: LLMGateway,
    question: str,
    sources: list[Source],
    memory_text: str,
    ctx: CallContext,
    strict: bool = False,
) -> RAGAnswer:
    system = ANSWER_SYSTEM_V1 + (STRICT_ADDENDUM if strict else "")
    parts = []
    if memory_text:
        parts.append(f"<conversation_memory>\n{memory_text}\n</conversation_memory>")
    parts.append(f"<sources>\n{format_sources(sources)}\n</sources>")
    parts.append(f"Question: {question}")
    result = gateway.generate(
        "answer", system, [{"role": "user", "content": "\n\n".join(parts)}], RAGAnswer, ctx, max_tokens=2000
    )
    return sanitize_output(result.parsed, sources)


def sanitize_output(answer: RAGAnswer, sources: list[Source]) -> RAGAnswer:
    """Output checks: no system-prompt leakage, no links that did not come from the sources."""
    if PROMPT_MARKER in answer.answer:
        return RAGAnswer(answer="I can't share that.", status="answered", confidence="low")
    known_text = " ".join(s.content for s in sources)
    for url in set(URL.findall(answer.answer)):
        if url.rstrip(".,;") not in known_text:
            answer.answer = answer.answer.replace(url, "[link removed]")
    return answer
