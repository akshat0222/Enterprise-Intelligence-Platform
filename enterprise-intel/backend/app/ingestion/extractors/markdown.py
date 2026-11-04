from markdown_it import MarkdownIt
from markdown_it.token import Token

from app.ingestion.blocks import Block, ExtractResult, IngestionError
from app.ingestion.extractors.txt import decode_bytes


def _inline_text(token: Token, counter: dict[str, int]) -> str:
    parts: list[str] = []
    for child in token.children or []:
        if child.type in ("text", "code_inline"):
            parts.append(child.content)
        elif child.type in ("softbreak",):
            parts.append(" ")
        elif child.type == "hardbreak":
            parts.append("\n")
        elif child.type == "image":
            counter["images"] += 1  # images and their alt text are dropped
    return "".join(parts).strip()


def extract(path: str) -> ExtractResult:
    with open(path, "rb") as f:
        text = decode_bytes(f.read())

    tokens = MarkdownIt("commonmark").enable("table").parse(text)
    counter = {"images": 0}
    blocks: list[Block] = []
    list_depth = 0
    rows: list[list[str]] = []

    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.type in ("bullet_list_open", "ordered_list_open"):
            list_depth += 1
        elif t.type in ("bullet_list_close", "ordered_list_close"):
            list_depth -= 1
        elif t.type == "heading_open":
            content = _inline_text(tokens[i + 1], counter)
            if content:
                blocks.append(Block("heading", content, level=int(t.tag[1])))
        elif t.type == "paragraph_open":
            content = _inline_text(tokens[i + 1], counter)
            if content:
                blocks.append(Block("paragraph", ("- " if list_depth else "") + content))
        elif t.type in ("fence", "code_block"):
            blocks.append(Block("code", t.content.rstrip()))
        elif t.type == "table_open":
            rows = []
        elif t.type == "tr_open":
            rows.append([])
        elif t.type == "inline" and rows is not None and i > 0 and tokens[i - 1].type in ("th_open", "td_open"):
            rows[-1].append(_inline_text(t, counter))
        elif t.type == "table_close":
            blocks.append(Block("table", meta={"rows": rows}))
            rows = []
        # html_block / html_inline are intentionally ignored
        i += 1

    if not blocks:
        raise IngestionError("File contains no text")
    return ExtractResult(blocks=blocks, pages=1, images_removed=counter["images"])
