import re

from app.ingestion.tokens import count_tokens

SMALL_TABLE_TOKENS = 400
MAX_LEVEL_ROWS = 10_000


def _cell(value: object) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip().replace("|", "\\|")


def _is_numeric(cell: str) -> bool:
    return bool(re.fullmatch(r"[-+$€£]?[\d,.]+%?", cell.strip())) if cell.strip() else False


def split_header(rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    """The first row is the header if most of its cells are non-numeric; otherwise use generic names."""
    first = rows[0]
    non_empty = [c for c in first if c.strip()]
    if non_empty and sum(not _is_numeric(c) for c in non_empty) / len(non_empty) > 0.5:
        return first, rows[1:]
    return [f"col{i + 1}" for i in range(len(first))], rows


def to_markdown(header: list[str], rows: list[list[str]]) -> str:
    width = len(header)
    lines = ["| " + " | ".join(header) + " |", "|" + " --- |" * width]
    for row in rows:
        row = (row + [""] * width)[:width]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def table_to_chunks(raw_rows: list[list[object]], heading: str) -> list[tuple[str, dict]]:
    """Return (markdown, metadata) pairs. Large tables are split by rows, repeating the header."""
    rows = [[_cell(c) for c in r] for r in raw_rows if any(_cell(c) for c in r)]
    if not rows:
        return []
    header, body = split_header(rows)
    prefix = f"{heading}\n\n" if heading else ""

    def build(part: list[list[str]]) -> tuple[str, dict]:
        meta = {"columns": header, "rows": [dict(zip(header, r, strict=False)) for r in part]}
        return prefix + to_markdown(header, part), meta

    whole = build(body)
    if count_tokens(whole[0]) <= SMALL_TABLE_TOKENS or len(body) <= 1:
        return [whole]

    chunks: list[tuple[str, dict]] = []
    part: list[list[str]] = []
    for row in body:
        trial = part + [row]
        if part and count_tokens(prefix + to_markdown(header, trial)) > SMALL_TABLE_TOKENS:
            chunks.append(build(part))
            part = [row]
        else:
            part = trial
    if part:
        chunks.append(build(part))
    return chunks
