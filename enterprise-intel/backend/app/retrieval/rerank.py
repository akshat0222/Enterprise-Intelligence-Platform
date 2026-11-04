from functools import lru_cache

from app.retrieval.hybrid import SearchHit

RERANK_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"


@lru_cache(maxsize=1)
def _model():
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    return TextCrossEncoder(RERANK_MODEL)


def rerank(query: str, hits: list[SearchHit]) -> list[SearchHit]:
    """Re-order candidates with a local cross-encoder (query and passage scored together, unlike bi-encoder search)."""
    if len(hits) < 2:
        return hits
    scores = list(_model().rerank(query, [h.content for h in hits]))
    for hit, score in zip(hits, scores, strict=True):
        hit.rerank_score = float(score)
    return sorted(hits, key=lambda h: h.rerank_score, reverse=True)
