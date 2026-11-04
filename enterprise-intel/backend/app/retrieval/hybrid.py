import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Session

from app.config import settings
from app.ingestion.stats import STOPWORDS
from app.retrieval.embedder import get_embedder

RRF_K = 60
CANDIDATES = 20
MAX_PER_DOCUMENT = 3  # diversify so conflicting documents both surface


@dataclass
class SearchHit:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    uploaded_at: datetime | None
    content: str
    heading_path: list[str] | None
    page_start: int | None
    page_end: int | None
    content_type: str | None
    flagged_injection: bool
    score: float  # RRF score
    similarity: float | None  # cosine similarity, if the chunk was found by vector search
    keyword_rank: float | None  # ts_rank, if found by keyword search
    rerank_score: float | None = None  # cross-encoder score, if reranking ran


@dataclass
class SearchResult:
    hits: list[SearchHit]
    insufficient_evidence: bool


_COLUMNS = """c.id, c.document_id, d.filename, d.created_at AS uploaded_at, c.content, c.heading_path, c.page_start, c.page_end,
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


# Question filler dilutes ts_rank (each unmatched OR term lowers it), so it is dropped from the keyword query.
QUERY_FILLER = STOPWORDS | set("a an is it of to in on at be do so if or as by we me my i say tell show give else please "
                               "explain describe mention said says does".split())


def keyword_query(query: str) -> str:
    """OR together the query's distinctive words so a natural-language question still matches on exact terms."""
    words = list(dict.fromkeys(re.findall(r"[A-Za-z0-9]+", query.lower())))
    distinctive = [w for w in words if w not in QUERY_FILLER]
    return " | ".join(distinctive or words)


def _run(db: Session, sql: str, params: dict, doc_ids: list[uuid.UUID] | None):
    stmt = text(sql)
    if doc_ids:
        stmt = stmt.bindparams(bindparam("doc_ids", type_=ARRAY(UUID(as_uuid=True))))
        params = {**params, "doc_ids": doc_ids}
    return db.execute(stmt, params).all()


def hybrid_search(
    db: Session,
    user_id: uuid.UUID,
    query: str,
    document_ids: list[uuid.UUID] | None = None,
    top_k: int = 8,
    mode: str = "hybrid",
    rerank: bool | None = None,
) -> SearchResult:
    """Vector + keyword search, both scoped to the user inside the SQL, fused with Reciprocal Rank Fusion.

    `mode` ("hybrid" | "vector" | "keyword") and `rerank` exist so the evaluation can compare strategies.
    """
    embedder = get_embedder()
    base = {"uid": user_id, "limit": CANDIDATES}
    filter_docs = bool(document_ids)

    vector_rows = []
    if mode in ("hybrid", "vector"):
        vector_rows = _run(
            db,
            _vector_sql(filter_docs),
            {**base, "qvec": str(embedder.embed_query(query)), "model": embedder.model},
            document_ids,
        )
    tsq = keyword_query(query)
    keyword_rows = []
    if mode in ("hybrid", "keyword") and tsq:
        keyword_rows = _run(db, _keyword_sql(filter_docs), {**base, "tsq": tsq}, document_ids)

    fused: dict[uuid.UUID, SearchHit] = {}
    for rows, kind in ((vector_rows, "vector"), (keyword_rows, "keyword")):
        for rank, row in enumerate(rows, start=1):
            hit = fused.get(row.id)
            if hit is None:
                hit = fused[row.id] = SearchHit(
                    row.id, row.document_id, row.filename, row.uploaded_at, row.content, row.heading_path, row.page_start,
                    row.page_end, row.content_type, row.flagged_injection, 0.0, None, None,
                )  # fmt: skip
            hit.score += 1 / (RRF_K + rank)
            if kind == "vector":
                hit.similarity = float(row.sim)
            else:
                hit.keyword_rank = float(row.rank)

    ordered = sorted(fused.values(), key=lambda h: h.score, reverse=True)
    if settings.rerank_enabled if rerank is None else rerank:
        from app.retrieval.rerank import rerank as cross_encode

        ordered = cross_encode(query, ordered[:CANDIDATES])
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
