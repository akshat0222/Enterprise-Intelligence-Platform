import hashlib
import os
import re

import fitz  # PyMuPDF
import magic

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md", ".csv"}
TEXT_MIMES = {"text/plain", "text/markdown", "text/x-markdown", "text/csv", "application/csv", "application/x-empty"}


class UploadValidationError(Exception):
    pass


def sanitize_filename(name: str) -> str:
    name = os.path.basename(name.replace("\\", "/"))
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return name[:255] or "document"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_upload(filename: str, data: bytes, max_bytes: int) -> str:
    """Check extension, size and real content type; return the file type (pdf|txt|md|csv)."""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadValidationError("Unsupported file type. Allowed: PDF, TXT, MD, CSV")
    if not data:
        raise UploadValidationError("File is empty")
    if len(data) > max_bytes:
        raise UploadValidationError(f"File exceeds the {max_bytes // (1024 * 1024)} MB limit")

    mime = magic.from_buffer(data[:8192], mime=True)
    if ext == ".pdf":
        if mime != "application/pdf":
            raise UploadValidationError("File content is not a PDF")
        try:
            doc = fitz.open(stream=data, filetype="pdf")
        except Exception:
            raise UploadValidationError("PDF is corrupt or unreadable") from None
        if doc.needs_pass or doc.is_encrypted:
            raise UploadValidationError("Encrypted PDFs are not supported")
        if doc.page_count == 0:
            raise UploadValidationError("PDF has no pages")
    else:
        if mime not in TEXT_MIMES:
            raise UploadValidationError(f"File content does not look like {ext[1:].upper()} text")
        if b"\x00" in data[:8192]:
            raise UploadValidationError("File contains binary data")
    return ext[1:]
