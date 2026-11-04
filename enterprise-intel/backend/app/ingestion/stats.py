import re
from collections import Counter

from app.ingestion.chunker import ChunkDraft
from app.ingestion.tokens import count_tokens

STOPWORDS = set(
    """about above after again against also because been before being below between both could did does
doing down during each from further had has have having here hers herself himself into itself just
more most myself once only other ours ourselves over own same should some such than that their
theirs them themselves then there these they this those through under until very was were what when
where which while who whom will with would your yours yourself yourselves the and for are but not
you all any can her him his its our out she too off how why""".split()
)


def compute_stats(
    chunks: list[ChunkDraft], pages: int, images_removed: int, outline: list[dict], tables_found: int
) -> dict:
    text = "\n".join(c.content for c in chunks)
    words = re.findall(r"[A-Za-z][A-Za-z'-]{3,}", text.lower())
    keywords = Counter(w for w in words if w not in STOPWORDS).most_common(15)
    return {
        "words": len(text.split()),
        "tokens": count_tokens(text),
        "characters": len(text),
        "pages": pages,
        "sections": len({tuple(c.heading_path) for c in chunks}),
        "headings": outline,
        "tables": tables_found,
        "images_removed": images_removed,
        "chunks": len(chunks),
        "keywords": [{"word": w, "count": n} for w, n in keywords],
    }
