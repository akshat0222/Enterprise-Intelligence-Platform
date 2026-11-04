import fitz  # PyMuPDF
import pdfplumber

from app.ingestion.blocks import Block, ExtractResult, IngestionError

MIN_CHARS_PER_PAGE = 20


def _inside(box: tuple[float, float, float, float], x0: float, y0: float, x1: float, y1: float) -> bool:
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]


def extract(path: str) -> ExtractResult:
    blocks: list[Block] = []
    images = 0
    chars = 0

    doc = fitz.open(path)
    pages = doc.page_count
    with pdfplumber.open(path) as plumber:
        for number, page in enumerate(doc, start=1):
            page_blocks: list[Block] = []
            table_boxes: list[tuple[float, float, float, float]] = []

            try:
                for table in plumber.pages[number - 1].find_tables():
                    rows = table.extract()
                    if rows:
                        table_boxes.append(table.bbox)
                        page_blocks.append(
                            Block("table", page=number, meta={"rows": rows, "y": table.bbox[1]})
                        )
            except Exception:
                pass  # table detection is best-effort; the text is still extracted below

            for raw in page.get_text("dict")["blocks"]:
                if raw["type"] == 1:
                    images += 1
                    continue
                for line in raw.get("lines", []):
                    spans = [s for s in line["spans"] if s["text"].strip()]
                    if not spans:
                        continue
                    x0, y0, x1, y1 = line["bbox"]
                    if any(_inside(box, x0, y0, x1, y1) for box in table_boxes):
                        continue
                    text = "".join(s["text"] for s in line["spans"]).strip()
                    chars += len(text)
                    page_blocks.append(
                        Block(
                            "line",
                            text,
                            page=number,
                            meta={
                                "size": round(max(s["size"] for s in spans), 1),
                                "bold": all(s["flags"] & 16 for s in spans),
                                "y": y0,
                                "page_height": page.rect.height,
                            },
                        )
                    )

            page_blocks.sort(key=lambda b: b.meta["y"])
            blocks.extend(page_blocks)

    if chars / max(pages, 1) < MIN_CHARS_PER_PAGE:
        raise IngestionError("No extractable text (scanned PDF). OCR not supported.")
    return ExtractResult(blocks=blocks, pages=pages, images_removed=images)
