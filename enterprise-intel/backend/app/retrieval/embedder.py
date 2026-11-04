import hashlib
import math
import re
import time
from functools import lru_cache
from typing import Protocol

import httpx

from app.config import settings

DIMENSIONS = 384  # must match chunks.embedding vector(384)
BATCH_SIZE = 64


class Embedder(Protocol):
    model: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Offline, deterministic bag-of-words embedder (feature hashing of unigrams and bigrams).

    Lexical rather than semantic, but real enough to exercise the whole retrieval stack without an API key.
    Use the OpenAI embedder for production quality.
    """

    model = "hashing-384"

    def _embed(self, text: str) -> list[float]:
        words = re.findall(r"\w+", text.lower())
        vec = [0.0] * DIMENSIONS
        for token in words + [f"{a}_{b}" for a, b in zip(words, words[1:], strict=False)]:
            h = int.from_bytes(hashlib.md5(token.encode()).digest()[:8], "big")
            vec[h % DIMENSIONS] += 1.0 if (h >> 63) & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class OpenAIEmbedder:
    model = "text-embedding-3-small-384"  # text-embedding-3-small, shortened to 384 dims
    api_model = "text-embedding-3-small"

    def __init__(self, api_key: str):
        self._client = httpx.Client(
            base_url="https://api.openai.com/v1", headers={"Authorization": f"Bearer {api_key}"}, timeout=30
        )

    def _request(self, inputs: list[str]) -> list[list[float]]:
        for attempt in range(4):
            payload = {"model": self.api_model, "input": inputs, "dimensions": DIMENSIONS}
            resp = self._client.post("/embeddings", json=payload)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(min(float(resp.headers.get("retry-after", 2**attempt)), 20))
                continue
            resp.raise_for_status()
            return [d["embedding"] for d in sorted(resp.json()["data"], key=lambda d: d["index"])]
        raise RuntimeError("unreachable")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH_SIZE):
            out.extend(self._request(texts[i : i + BATCH_SIZE]))
        return out

    def embed_query(self, text: str) -> list[float]:
        return self._request([text])[0]


class LocalEmbedder:
    """all-MiniLM-L6-v2 via ONNX (fastembed): free, offline, no API key. 384 dimensions."""

    model = "all-MiniLM-L6-v2"

    def __init__(self):
        from fastembed import TextEmbedding

        self._model = TextEmbedding("sentence-transformers/all-MiniLM-L6-v2")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(texts, batch_size=BATCH_SIZE)]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


@lru_cache
def get_embedder() -> Embedder:
    if settings.embedding_provider == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("EMBEDDING_PROVIDER=openai requires OPENAI_API_KEY")
        return OpenAIEmbedder(settings.openai_api_key)
    if settings.embedding_provider == "hash":
        return HashingEmbedder()
    return LocalEmbedder()


def contextual_text(title: str, heading_path: list[str] | None, content: str) -> str:
    """Embedded text carries its document and section so short chunks stay retrievable."""
    path = " > ".join([title, *(heading_path or [])])
    return f"Document: {path}\n{content}"
