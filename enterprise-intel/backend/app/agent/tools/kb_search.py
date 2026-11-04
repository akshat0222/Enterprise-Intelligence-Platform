import uuid

from pydantic import BaseModel, Field

from app.agent.registry import ToolContext, ToolResult, register_tool
from app.db.session import SessionLocal
from app.rag.sources import merge_hits
from app.retrieval.hybrid import hybrid_search


class KBSearchArgs(BaseModel):
    query: str = Field(min_length=3, max_length=500)
    document_ids: list[uuid.UUID] = []
    top_k: int = Field(8, ge=1, le=20)


@register_tool(
    "knowledge_base_search",
    KBSearchArgs,
    "Search the user's documents (semantic + keyword). Returns relevant passages, each with a source id like S1.",
    timeout_s=20,
)
def knowledge_base_search(args: KBSearchArgs, t: ToolContext) -> ToolResult:
    # Scope: the model's ids if given, else the UI's document scope. The SQL still restricts to the user's own rows.
    doc_ids = args.document_ids or t.state.get("doc_scope") or None
    with SessionLocal() as db:
        result = hybrid_search(db, t.user_id, args.query, doc_ids, args.top_k)

    if result.insufficient_evidence:
        return ToolResult({"found": 0, "note": "no relevant passages"}, insufficient=True)

    sources = merge_hits(t.state["sources"], result.hits)
    by_chunk = {s.chunk_id: s for s in sources}
    passages = [
        {
            "source_id": by_chunk[h.chunk_id].id,
            "chunk_id": str(h.chunk_id),
            "document": h.filename,
            "page": h.page_start,
            "section": " > ".join(h.heading_path or []),
            "score": round(h.score, 4),
            "preview": h.content[:160],
        }
        for h in result.hits
    ]
    return ToolResult({"found": len(passages), "passages": passages}, update={"sources": sources})
