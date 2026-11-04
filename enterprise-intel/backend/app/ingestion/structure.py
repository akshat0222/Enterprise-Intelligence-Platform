import re
from collections import Counter
from dataclasses import dataclass, field

from app.ingestion.blocks import Block

MAX_HEADING_WORDS = 12
NUMBERED = re.compile(r"^(?:chapter\s+\d+\b|(\d+(?:\.\d+)*)[.)]?\s+\S)", re.IGNORECASE)
UNDERLINE = re.compile(r"^(=|-){3,}$")


@dataclass
class Section:
    heading_path: list[str]
    level: int
    blocks: list[Block] = field(default_factory=list)

    @property
    def page_start(self) -> int | None:
        pages = [b.page for b in self.blocks if b.page]
        return min(pages) if pages else None

    @property
    def page_end(self) -> int | None:
        pages = [b.page for b in self.blocks if b.page]
        return max(pages) if pages else None


def _words(text: str) -> int:
    return len(text.split())


def classify_pdf_lines(blocks: list[Block]) -> None:
    """Heading = font size >= 1.15x body size, or bold, short and without a trailing period."""
    weights: Counter[float] = Counter()
    for b in blocks:
        if b.type == "line":
            weights[b.meta["size"]] += len(b.text)
    if not weights:
        return
    body = weights.most_common(1)[0][0]

    big_sizes = sorted(
        {b.meta["size"] for b in blocks if b.type == "line" and b.meta["size"] >= body * 1.15 and _words(b.text) <= 20},
        reverse=True,
    )
    size_level = {size: i + 1 for i, size in enumerate(big_sizes)}
    bold_level = min(len(big_sizes) + 1, 6)

    for b in blocks:
        if b.type != "line":
            continue
        if b.meta["size"] in size_level:
            b.type, b.level = "heading", size_level[b.meta["size"]]
        elif b.meta["bold"] and _words(b.text) < MAX_HEADING_WORDS and not b.text.endswith("."):
            b.type, b.level = "heading", bold_level


def _looks_like_txt_heading(text: str, prev_blank: bool, next_blank: bool) -> int:
    """Return a heading level (1-3) or 0 if the line is not a heading."""
    if _words(text) >= MAX_HEADING_WORDS or text.endswith((".", ",", ";")):
        return 0
    letters = [c for c in text if c.isalpha()]
    if letters and text.upper() == text and len(letters) >= 3:
        return 1
    m = NUMBERED.match(text)
    if m and prev_blank and next_blank:  # numbered lines inside a run of lines are list items, not headings
        return min(1 + (m.group(1) or "").count("."), 3)
    if prev_blank and next_blank and text.istitle():
        return 2
    return 0


def classify_txt_lines(blocks: list[Block]) -> list[Block]:
    out: list[Block] = []
    for i, b in enumerate(blocks):
        if b.type != "line":
            out.append(b)
            continue
        if UNDERLINE.match(b.text) and out and out[-1].type in ("line", "heading"):
            prev = out[-1]
            prev.type, prev.level = "heading", 1 if b.text[0] == "=" else 2
            continue
        prev_blank = i == 0 or blocks[i - 1].type == "blank"
        next_blank = i + 1 >= len(blocks) or blocks[i + 1].type == "blank"
        level = _looks_like_txt_heading(b.text, prev_blank, next_blank)
        if level:
            b.type, b.level = "heading", level
        out.append(b)
    return out


def merge_lines(blocks: list[Block]) -> list[Block]:
    """Join consecutive text lines into paragraphs, repairing hyphenated line breaks."""
    out: list[Block] = []
    buf: list[Block] = []

    def flush() -> None:
        if not buf:
            return
        text = buf[0].text
        for b in buf[1:]:
            if text.endswith("-") and b.text[:1].islower():
                text = text[:-1] + b.text
            else:
                text += " " + b.text
        out.append(Block("paragraph", text, page=buf[0].page))
        buf.clear()

    for b in blocks:
        if b.type == "line":
            if buf and buf[-1].page != b.page:
                flush()
            buf.append(b)
        else:
            flush()
            if b.type != "blank":
                out.append(b)
    flush()
    return out


def build_sections(blocks: list[Block], file_type: str) -> tuple[list[Section], list[dict]]:
    """Return the section tree and the flat heading outline."""
    if file_type == "pdf":
        classify_pdf_lines(blocks)
    elif file_type == "txt":
        blocks = classify_txt_lines(blocks)
    blocks = merge_lines(blocks)

    sections: list[Section] = []
    outline: list[dict] = []
    stack: list[tuple[int, str]] = []
    current = Section([], 0)
    for b in blocks:
        if b.type == "heading":
            outline.append({"level": b.level, "text": b.text, "page": b.page})
            if current.blocks:
                sections.append(current)
            while stack and stack[-1][0] >= b.level:
                stack.pop()
            stack.append((b.level, b.text))
            current = Section([t for _, t in stack], b.level)
        else:
            current.blocks.append(b)
    if current.blocks:
        sections.append(current)
    return sections, outline
