import chardet

from app.ingestion.blocks import Block, ExtractResult, IngestionError


def decode_bytes(data: bytes) -> str:
    encoding = chardet.detect(data[:100_000])["encoding"] or "utf-8"
    try:
        return data.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        return data.decode("utf-8", errors="replace")


def extract(path: str) -> ExtractResult:
    with open(path, "rb") as f:
        text = decode_bytes(f.read())
    blocks = [
        Block("line", line.strip()) if line.strip() else Block("blank")
        for line in text.splitlines()
    ]
    if not any(b.type == "line" for b in blocks):
        raise IngestionError("File contains no text")
    return ExtractResult(blocks=blocks, pages=1)
