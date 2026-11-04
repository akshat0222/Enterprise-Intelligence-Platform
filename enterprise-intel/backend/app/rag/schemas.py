from typing import Literal

from pydantic import BaseModel, Field


class Citation(BaseModel):
    source_id: str
    quote: str = ""


class Conflict(BaseModel):
    topic: str
    sources: list[str] = []
    description: str


class RAGAnswer(BaseModel):
    answer: str = Field(description="Markdown answer with [S1]-style citation markers")
    citations: list[Citation] = []
    confidence: Literal["high", "medium", "low"] = "medium"
    status: Literal["answered", "insufficient_evidence", "conflicting_sources"] = "answered"
    conflicts: list[Conflict] = []


class RouterDecision(BaseModel):
    route: Literal["direct", "rag", "agent"]
    reason: str = ""


class Rewritten(BaseModel):
    query: str
    refers_to_previous_documents: bool = False


class ClaimCheck(BaseModel):
    claim: str
    verdict: Literal["yes", "no", "partial"]


class Judgement(BaseModel):
    claims: list[ClaimCheck]
