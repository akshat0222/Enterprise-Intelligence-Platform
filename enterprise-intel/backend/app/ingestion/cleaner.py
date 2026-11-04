import re
import unicodedata
from collections import defaultdict

from app.ingestion.blocks import Block

CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
PAGE_NUMBER = re.compile(r"^\s*(page\s+)?\d+(\s*(of|/)\s*\d+)?\s*$", re.IGNORECASE)
EDGE_FRACTION = 0.08  # top/bottom share of the page where headers, footers and page numbers live
MIN_PAGES_FOR_REPEAT_DETECTION = 3


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = CONTROL_CHARS.sub("", text)
    return re.sub(r"[ \t ]+", " ", text).strip()


def _signature(block: Block) -> tuple[str, int]:
    """Same text (digits ignored) at roughly the same vertical position."""
    h = block.meta.get("page_height") or 1
    return re.sub(r"\d+", "#", block.text.lower()), round(block.meta["y"] / h * 50)


def _at_edge(block: Block) -> bool:
    h = block.meta.get("page_height") or 1
    y = block.meta["y"] / h
    return y < EDGE_FRACTION or y > 1 - EDGE_FRACTION


def remove_repeated_lines(blocks: list[Block], pages: int) -> list[Block]:
    """Drop page numbers and lines that repeat on more than half of the pages (headers/footers)."""
    pdf_lines = [b for b in blocks if b.type == "line" and "y" in b.meta and b.page]
    if not pdf_lines:
        return blocks

    drop: set[int] = {id(b) for b in pdf_lines if _at_edge(b) and PAGE_NUMBER.match(b.text)}

    if pages >= MIN_PAGES_FOR_REPEAT_DETECTION:
        seen: dict[tuple[str, int], set[int]] = defaultdict(set)
        for b in pdf_lines:
            seen[_signature(b)].add(b.page)
        repeated = {sig for sig, where in seen.items() if len(where) / pages > 0.5}
        drop |= {id(b) for b in pdf_lines if _signature(b) in repeated}

    return [b for b in blocks if id(b) not in drop]


def clean(blocks: list[Block], pages: int) -> list[Block]:
    blocks = remove_repeated_lines(blocks, pages)
    for b in blocks:
        if b.type != "code" and b.text:
            b.text = normalize_text(b.text)
    return [b for b in blocks if b.type in ("blank", "table") or b.text]
