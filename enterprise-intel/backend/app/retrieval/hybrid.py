import re
import uuid
from dataclasses import dataclass

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Session

from app.config import settings
from app.retrieval.embedder import get_embedder

RRF_K = 60
CANDIDATES = 20
MAX_PER_DOCUMENT = 3  # diversify so conflicting documents both surface


@dataclass
class SearchHit:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    content: str
    heading_path: list[str] | None
    page_start: int | None
    page_end: int | None
    content_type: str | None
    flagged_injection: bool
    score: float  # RRF score
    similarity: float | None  # cosine similarity, if the chunk was found by vector search
    keyword_rank: float | None  # ts_rank, if found by keyword search


@dataclass
class SearchResult:
    hits: list[SearchHit]
    insufficient_evidence: bool


_COLUMNS = """c.id, c.document_id, d.filename, c.content, c.heading_path, c.page_start, c.page_end,
       c.content_type, c.flagged_injection"""


def _vector_sql(filter_docs: bool) -> str:
    return f"""
    SELECT {_COLUMNS}, 1 - (c.embedding <=> CAST(:qvec AS vector)) AS sim
    FROM chunks c JOIN documents d ON d.id = c.document_id
    WHERE c.user_id = :uid AND d.user_id = :uid AND c.embedding IS NOT NULL AND c.embedding_model = :model
      {"AND c.document_id = ANY(:doc_ids)" if filter_docs else ""}
    ORDER BY c.embedding <=> CAST(:qvec AS vector)
    LIMIT :limit"""


def _keyword_sql(filter_docs: bool) -> str:
    return f"""
    SELECT {_COLUMNS}, ts_rank(c.tsv, q.query) AS rank
    FROM chunks c JOIN documents d ON d.id = c.document_id,
         (SELECT to_tsquery('english', :tsq) AS query) q
    WHERE c.user_id = :uid AND d.user_id = :uid AND c.tsv @@ q.query
      {"AND c.document_id = ANY(:doc_ids)" if filter_docs else ""}
    ORDER BY rank DESC
    LIMIT :limit"""


def keyword_query(query: str) -> str:
    """OR together the query's words so a natural-language question still matches on its distinctive terms."""
    words = dict.fromkeys(re.findall(r"[A-Za-z0-9]+", query.lower()))
    return " | ".join(words)


def _run(db: Session, sql: str, params: dict, doc_ids: list[uuid.UUID] | None):
    stmt = text(sql)
    if doc_ids:
        stmt = stmt.bindparams(bindparam("doc_ids", type_=ARRAY(UUID(as_uuid=True))))
        params = {**params, "doc_ids": doc_ids}
    return db.execute(stmt, params).all()


def hybrid_search(
    db: Session, user_id: uuid.UUID, query: str, document_ids: list[uuid.UUID] | None = None, top_k: int = 8
) -> SearchResult:
    """Vector + keyword search, both scoped to the user inside the SQL, fused with Reciprocal Rank Fusion."""
    embedder = get_embedder()
    base = {"uid": user_id, "limit": CANDIDATES}
    filter_docs = bool(document_ids)

    vector_rows = _run(
        db,
        _vector_sql(filter_docs),
        {**base, "qvec": str(embedder.embed_query(query)), "model": embedder.model},
        document_ids,
    )
    tsq = keyword_query(query)
    keyword_rows = _run(db, _keyword_sql(filter_docs), {**base, "tsq": tsq}, document_ids) if tsq else []

    fused: dict[uuid.UUID, SearchHit] = {}
    for rows, kind in ((vector_rows, "vector"), (keyword_rows, "keyword")):
        for rank, row in enumerate(rows, start=1):
            hit = fused.get(row.id)
            if hit is None:
                hit = fused[row.id] = SearchHit(
                    row.id, row.document_id, row.filename, row.content, row.heading_path, row.page_start,
                    row.page_end, row.content_type, row.flagged_injection, 0.0, None, None,
                )  # fmt: skip
            hit.score += 1 / (RRF_K + rank)
            if kind == "vector":
                hit.similarity = float(row.sim)
            else:
                hit.keyword_rank = float(row.rank)

    ordered = sorted(fused.values(), key=lambda h: h.score, reverse=True)
    per_doc: dict[uuid.UUID, int] = {}
    hits: list[SearchHit] = []
    for hit in ordered:
        if per_doc.get(hit.document_id, 0) >= MAX_PER_DOCUMENT:
            continue
        per_doc[hit.document_id] = per_doc.get(hit.document_id, 0) + 1
        hits.append(hit)
        if len(hits) == top_k:
            break

    return SearchResult(hits=hits, insufficient_evidence=not _relevant(hits))


def _relevant(hits: list[SearchHit]) -> bool:
    """Relevance gate: thresholds are settings and must be tuned on the evaluation set."""
    return any(
        (h.similarity is not None and h.similarity >= settings.min_similarity)
        or (h.keyword_rank is not None and h.keyword_rank >= settings.min_keyword_rank)
        for h in hits
    )
