import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, update

from app.config import settings
from app.db.models import Chunk, Document
from app.db.session import SessionLocal
from app.ingestion import cleaner
from app.ingestion.blocks import IngestionError
from app.ingestion.chunker import ChunkDraft, chunk_sections
from app.ingestion.extractors import extract
from app.ingestion.stats import compute_stats
from app.ingestion.structure import build_sections
from app.logging_utils import log_activity
from app.rag.injection import looks_like_injection


def absolute_path(storage_path: str) -> str:
    return os.path.join(settings.upload_dir, storage_path)


def run_pipeline(path: str, file_type: str) -> tuple[list[ChunkDraft], dict]:
    """Deterministic extract -> clean -> structure -> chunk -> stats. No AI involved."""
    result = extract(path, file_type)
    blocks = cleaner.clean(result.blocks, result.pages)
    tables_found = sum(1 for b in blocks if b.type == "table")
    sections, outline = build_sections(blocks, file_type)
    chunks = chunk_sections(sections, settings.chunk_target_tokens, settings.chunk_overlap_tokens)
    if not chunks:
        raise IngestionError("No usable text found in document")
    return chunks, compute_stats(chunks, result.pages, result.images_removed, outline, tables_found)


def process_document(document_id: uuid.UUID, job_id: str | None = None) -> None:
    """Background job. Idempotent: an atomic status claim ensures a document is never processed twice at once."""
    job_id = job_id or uuid.uuid4().hex
    with SessionLocal() as db:
        claimed = db.execute(
            update(Document)
            .where(Document.id == document_id, Document.status == "uploaded")
            .values(status="processing", processing_job_id=job_id, error_message=None)
        ).rowcount
        db.commit()
        if not claimed:
            return

        doc = db.get(Document, document_id)
        try:
            chunks, stats = run_pipeline(absolute_path(doc.storage_path), doc.file_type)
            db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
            db.add_all(
                Chunk(
                    document_id=doc.id,
                    user_id=doc.user_id,
                    chunk_index=i,
                    content=c.content,
                    heading_path=c.heading_path,
                    page_start=c.page_start,
                    page_end=c.page_end,
                    content_type=c.content_type,
                    token_count=c.token_count,
                    word_count=c.word_count,
                    chunk_metadata=c.metadata,
                    flagged_injection=looks_like_injection(c.content),
                )
                for i, c in enumerate(chunks)
            )
            doc.stats = {**stats, "flagged_chunks": sum(looks_like_injection(c.content) for c in chunks)}
            doc.status = "ready"
            doc.processed_at = datetime.now(timezone.utc)
            db.commit()
            log_activity(db, doc.user_id, "process_document", "document", doc.id, {"chunks": len(chunks)})
        except Exception as exc:
            db.rollback()
            message = str(exc) if isinstance(exc, IngestionError) else f"Processing failed: {type(exc).__name__}"
            db.execute(
                update(Document)
                .where(Document.id == document_id)
                .values(status="failed", error_message=message[:500], processed_at=datetime.now(timezone.utc))
            )
            db.commit()
            log_activity(db, doc.user_id, "process_document", "document", document_id, {"error": str(exc)[:500]}, "error")
