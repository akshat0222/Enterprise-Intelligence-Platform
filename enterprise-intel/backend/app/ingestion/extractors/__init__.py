from app.ingestion.blocks import ExtractResult, IngestionError
from app.ingestion.extractors import csv_file, markdown, pdf, txt

EXTRACTORS = {
    "pdf": pdf.extract,
    "txt": txt.extract,
    "md": markdown.extract,
    "csv": csv_file.extract,
}


def extract(path: str, file_type: str) -> ExtractResult:
    try:
        extractor = EXTRACTORS[file_type]
    except KeyError:
        raise IngestionError(f"Unsupported file type: {file_type}") from None
    return extractor(path)
