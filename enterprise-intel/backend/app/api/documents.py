import os
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.config import settings
from app.db.models import Chunk, Document, User
from app.db.session import get_db
from app.ingestion.pipeline import absolute_path, process_document
from app.ingestion.validators import UploadValidationError, sanitize_filename, sha256_hex, validate_upload
from app.logging_utils import log_activity

router = APIRouter(prefix="/documents", tags=["documents"])


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    file_type: str
    size_bytes: int | None
    status: str
    error_message: str | None
    stats: dict | None
    created_at: datetime
    processed_at: datetime | None


class UploadOut(DocumentOut):
    duplicate: bool = False


class ChunkOut(BaseModel):
    id: uuid.UUID
    chunk_index: int
    content: str
    heading_path: list[str] | None
    page_start: int | None
    page_end: int | None
    content_type: str | None
    token_count: int | None
    flagged_injection: bool


def get_owned_document(db: Session, user: User, document_id: uuid.UUID) -> Document:
    """Ownership is part of the query; a document that belongs to someone else is indistinguishable from a missing one."""
    doc = db.scalar(select(Document).where(Document.id == document_id, Document.user_id == user.id))
    if doc is None:
        raise HTTPException(404, "Document not found")
    return doc


def _remove_file(storage_path: str) -> None:
    try:
        os.remove(absolute_path(storage_path))
    except FileNotFoundError:
        pass


@router.post("", response_model=UploadOut, status_code=202)
async def upload_document(
    file: UploadFile,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    max_bytes = settings.max_upload_mb * 1024 * 1024
    data = await file.read(max_bytes + 1)
    filename = sanitize_filename(file.filename or "document")
    try:
        file_type = validate_upload(filename, data, max_bytes)
    except UploadValidationError as exc:
        raise HTTPException(400, str(exc))

    content_hash = sha256_hex(data)

    def existing() -> Document | None:
        return db.scalar(select(Document).where(Document.user_id == user.id, Document.content_hash == content_hash))

    def duplicate_response(doc: Document) -> JSONResponse:
        body = UploadOut.model_validate(doc).model_copy(update={"duplicate": True})
        return JSONResponse(jsonable_encoder(body), status_code=200)

    if (doc := existing()) is not None:
        return duplicate_response(doc)

    storage_path = f"{user.id}/{uuid.uuid4()}.{file_type}"
    full_path = absolute_path(storage_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "wb") as f:
        f.write(data)

    doc = Document(
        user_id=user.id,
        filename=filename,
        file_type=file_type,
        storage_path=storage_path,
        size_bytes=len(data),
        content_hash=content_hash,
    )
    db.add(doc)
    try:
        db.commit()
    except IntegrityError:  # a parallel upload of the same file won the unique constraint
        db.rollback()
        _remove_file(storage_path)
        return duplicate_response(existing())

    log_activity(db, user.id, "upload", "document", doc.id, {"filename": filename, "size": len(data)})
    background.add_task(process_document, doc.id)
    return UploadOut.model_validate(doc)


@router.get("", response_model=list[DocumentOut])
def list_documents(
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = select(Document).where(Document.user_id == user.id)
    if status:
        query = query.where(Document.status == status)
    return db.scalars(query.order_by(Document.created_at.desc()).limit(limit).offset(offset)).all()


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(document_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return get_owned_document(db, user, document_id)


@router.get("/{document_id}/chunks", response_model=list[ChunkOut])
def get_chunks(
    document_id: uuid.UUID,
    after: int = Query(-1, description="Return chunks with chunk_index greater than this"),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_owned_document(db, user, document_id)
    return db.scalars(
        select(Chunk)
        .where(Chunk.document_id == document_id, Chunk.user_id == user.id, Chunk.chunk_index > after)
        .order_by(Chunk.chunk_index)
        .limit(limit)
    ).all()


@router.post("/{document_id}/reprocess", response_model=DocumentOut, status_code=202)
def reprocess_document(
    document_id: uuid.UUID,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    doc = get_owned_document(db, user, document_id)
    # Atomic transition: only one concurrent request can move a finished document back to "uploaded".
    moved = db.execute(
        update(Document)
        .where(Document.id == doc.id, Document.status.in_(["failed", "ready"]))
        .values(status="uploaded", error_message=None)
    ).rowcount
    db.commit()
    if not moved:
        raise HTTPException(409, "Document is already being processed")
    background.add_task(process_document, doc.id)
    db.refresh(doc)
    return doc


@router.delete("/{document_id}", status_code=204)
def delete_document(document_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    doc = get_owned_document(db, user, document_id)
    storage_path, doc_id = doc.storage_path, doc.id
    db.delete(doc)  # chunks are removed by ON DELETE CASCADE
    db.commit()
    _remove_file(storage_path)
    log_activity(db, user.id, "delete_doc", "document", doc_id)
