import re
from dataclasses import dataclass, field

from app.ingestion.structure import Section
from app.ingestion.tables import table_to_chunks
from app.ingestion.tokens import count_tokens

SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"\'(\[])')


@dataclass
class ChunkDraft:
    content: str
    heading_path: list[str]
    page_start: int | None
    page_end: int | None
    content_type: str
    token_count: int
    word_count: int
    metadata: dict = field(default_factory=dict)


@dataclass
class _Unit:
    text: str
    tokens: int
    page: int | None
    paragraph_start: bool


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_SPLIT.split(text) if s.strip()]


def _split_long(sentence: str, limit: int) -> list[str]:
    """A single sentence longer than the target is cut on word boundaries."""
    words, parts, cur = sentence.split(), [], []
    for w in words:
        cur.append(w)
        if count_tokens(" ".join(cur)) >= limit:
            parts.append(" ".join(cur))
            cur = []
    if cur:
        parts.append(" ".join(cur))
    return parts


def _units(section: Section, target: int) -> list[_Unit]:
    units: list[_Unit] = []
    for block in section.blocks:
        if block.type not in ("paragraph", "code"):
            continue
        sentences = [block.text] if block.type == "code" else split_sentences(block.text)
        first = True
        for sentence in sentences:
            for piece in _split_long(sentence, target) if count_tokens(sentence) > target else [sentence]:
                units.append(_Unit(piece, count_tokens(piece), block.page, first))
                first = False
    return units


def _join(units: list[_Unit]) -> str:
    out = ""
    for i, u in enumerate(units):
        out += u.text if i == 0 else ("\n\n" if u.paragraph_start else " ") + u.text
    return out


def _make(content: str, section: Section, ctype: str, pages: list[int | None], meta: dict | None = None) -> ChunkDraft:
    real = [p for p in pages if p]
    return ChunkDraft(
        content=content,
        heading_path=section.heading_path,
        page_start=min(real) if real else None,
        page_end=max(real) if real else None,
        content_type=ctype,
        token_count=count_tokens(content),
        word_count=len(content.split()),
        metadata=meta or {},
    )


def _text_chunks(section: Section, target: int, overlap: int) -> list[ChunkDraft]:
    units = _units(section, target)
    chunks: list[ChunkDraft] = []
    cur: list[_Unit] = []
    size = 0
    fresh = 0  # units in `cur` that are not just carried-over overlap

    def emit() -> None:
        chunks.append(_make(_join(cur), section, "text", [u.page for u in cur]))

    for u in units:
        if cur and fresh and size + u.tokens > target:
            emit()
            tail: list[_Unit] = []
            total = 0
            for prev in reversed(cur):
                if total + prev.tokens > overlap:
                    break
                tail.insert(0, prev)
                total += prev.tokens
            cur, size, fresh = tail, total, 0
        cur.append(u)
        size += u.tokens
        fresh += 1
    if cur and fresh:
        emit()
    return chunks


def _csv_chunks(section: Section, target: int) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []
    rows: list[str] = []
    first_row = last_row = 0
    size = 0

    def emit() -> None:
        chunks.append(
            _make("\n\n".join(rows), section, "csv_rows", [None], {"row_start": first_row, "row_end": last_row})
        )

    for block in section.blocks:
        if block.type == "csv_schema":
            chunks.append(_make(block.text, section, "csv_schema", [None], block.meta))
        elif block.type == "csv_row":
            t = count_tokens(block.text)
            if rows and size + t > target:
                emit()
                rows, size = [], 0
            if not rows:
                first_row = block.meta["row"]
            rows.append(block.text)
            last_row = block.meta["row"]
            size += t
    if rows:
        emit()
    return chunks


def chunk_sections(sections: list[Section], target: int, overlap: int) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []
    for section in sections:
        if any(b.type in ("csv_schema", "csv_row") for b in section.blocks):
            chunks.extend(_csv_chunks(section, target))
            continue
        chunks.extend(_text_chunks(section, target, overlap))
        heading = " > ".join(section.heading_path)
        for block in section.blocks:
            if block.type == "table":
                for markdown, meta in table_to_chunks(block.meta["rows"], heading):
                    chunks.append(_make(markdown, section, "table", [block.page], meta))
    return chunks
