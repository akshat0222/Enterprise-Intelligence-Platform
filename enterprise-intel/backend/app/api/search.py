import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.retrieval.hybrid import hybrid_search

router = APIRouter(tags=["search"])


class SearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=500)
    document_ids: list[uuid.UUID] = []
    top_k: int = Field(8, ge=1, le=20)


class HitOut(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    content: str
    heading_path: list[str] | None
    page_start: int | None
    page_end: int | None
    content_type: str | None
    flagged_injection: bool
    score: float
    similarity: float | None
    keyword_rank: float | None


class SearchResponse(BaseModel):
    insufficient_evidence: bool
    hits: list[HitOut]


@router.post("/search", response_model=SearchResponse)
def search(body: SearchRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    result = hybrid_search(db, user.id, body.query, body.document_ids or None, body.top_k)
    return SearchResponse(
        insufficient_evidence=result.insufficient_evidence,
        hits=[HitOut(**vars(h)) for h in result.hits],
    )
