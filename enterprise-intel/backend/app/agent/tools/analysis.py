import re
import uuid
from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agent.registry import ToolContext, ToolError, ToolResult, register_tool
from app.agent.schemas import AnalysisResult
from app.db.models import Chunk, Document
from app.db.session import SessionLocal
from app.ingestion.stats import STOPWORDS
from app.prompts import ANALYSIS_SYSTEM_V1
from app.rag.sources import Source, format_sources

MAX_CHUNKS = 25


class AnalysisArgs(BaseModel):
    analysis_type: Literal["summary", "themes", "root_causes", "compare", "extract_entities", "find_conflicts"]
    chunk_ids: list[uuid.UUID] = []
    document_id: uuid.UUID | None = None
    focus: str = Field("", max_length=300)


def _source_from_row(chunk: Chunk, filename: str, uploaded_at, existing: list[Source]) -> Source:
    for s in existing:
        if s.chunk_id == chunk.id:
            return s
    return Source(
        id=f"S{len(existing) + 1}", chunk_id=chunk.id, document_id=chunk.document_id, filename=filename,
        content=chunk.content, page=chunk.page_start, section=" > ".join(chunk.heading_path or []),
        flagged=chunk.flagged_injection, uploaded_at=uploaded_at,
    )  # fmt: skip


def _resolve(args: AnalysisArgs, t: ToolContext) -> tuple[list[Source], list[Source]]:
    """Return (sources to analyse, updated global source list). Every lookup is scoped to the user."""
    known: list[Source] = list(t.state["sources"])
    if not args.chunk_ids and not args.document_id:
        return known[:MAX_CHUNKS], known

    query = select(Chunk, Document.filename, Document.created_at).join(Document, Document.id == Chunk.document_id)
    query = query.where(Chunk.user_id == t.user_id, Document.user_id == t.user_id)
    if args.chunk_ids:
        query = query.where(Chunk.id.in_(args.chunk_ids))
    else:
        query = query.where(Chunk.document_id == args.document_id).order_by(Chunk.chunk_index)
    with SessionLocal() as db:
        rows = db.execute(query.limit(MAX_CHUNKS)).all()

    chosen: list[Source] = []
    for chunk, filename, uploaded in rows:
        source = _source_from_row(chunk, filename, uploaded, known)
        if source not in known:
            known.append(source)
        chosen.append(source)
    return chosen, known


def _stats(sources: list[Source]) -> dict:
    text = " ".join(s.content for s in sources).lower()
    words = [w for w in re.findall(r"[a-z][a-z'-]{3,}", text) if w not in STOPWORDS]
    return {
        "passages": len(sources),
        "documents": len({s.document_id for s in sources}),
        "total_words": len(text.split()),
        "top_keywords": Counter(words).most_common(12),
    }


@register_tool(
    "document_analysis",
    AnalysisArgs,
    "Analyse passages (summary, themes, root_causes, compare, extract_entities, find_conflicts). Omit chunk_ids to "
    "analyse the passages found by knowledge_base_search.",
    timeout_s=60,
)
def document_analysis(args: AnalysisArgs, t: ToolContext) -> ToolResult:
    sources, all_sources = _resolve(args, t)
    if not sources:
        raise ToolError("no passages to analyse; run knowledge_base_search first")

    stats = _stats(sources)  # deterministic facts, computed without an LLM
    prompt = (
        f"Analysis type: {args.analysis_type}\nFocus: {args.focus or 'general'}\n"
        f"Deterministic statistics: {stats}\n\n<sources>\n{format_sources(sources, 1500)}\n</sources>"
    )
    result = t.gateway.generate(
        "analysis", ANALYSIS_SYSTEM_V1, [{"role": "user", "content": prompt}], AnalysisResult, t.ctx, 3000
    )
    analysis: AnalysisResult = result.parsed
    analysis.analysis_type = args.analysis_type
    valid = {s.id for s in sources}
    for f in analysis.findings:
        f.evidence = [e for e in f.evidence if e in valid]

    summary = {
        "analysis_type": args.analysis_type,
        "findings": len(analysis.findings),
        "top_findings": [f.point[:100] for f in analysis.findings[:3]],
        "conflicts": len(analysis.conflicts),
        "gaps": len(analysis.gaps),
        "stats": stats,
    }
    return ToolResult(summary, update={"analysis": analysis, "sources": all_sources})
