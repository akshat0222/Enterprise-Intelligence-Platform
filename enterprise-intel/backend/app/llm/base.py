import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel

Message = dict[str, str]  # {"role": "user" | "assistant", "content": "..."}


class LLMError(Exception):
    """A failed provider call. `retryable` errors are retried; `auth` errors skip straight to the fallback."""

    def __init__(self, message: str, *, retryable: bool = False, auth: bool = False, retry_after: float | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.auth = auth
        self.retry_after = retry_after


class LLMUnavailable(Exception):
    """Every provider in the route failed."""


@dataclass
class RawResult:
    text: str
    json: dict | None
    input_tokens: int
    output_tokens: int
    finish_reason: str | None = None


@dataclass
class LLMResult:
    text: str
    parsed: BaseModel | None
    input_tokens: int
    output_tokens: int
    model: str
    provider: str
    latency_ms: int
    finish_reason: str | None
    fallback_used: bool
    cost_usd: float


@dataclass
class CallContext:
    """Who a call is for, plus running totals for the chat turn (tokens, models, fallbacks)."""

    user_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    message_id: uuid.UUID | None = None
    trace_id: str | None = None
    preferred_model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    fallback_used: bool = False
    models: set[str] = field(default_factory=set)


class LLMProvider(Protocol):
    name: str

    def generate(
        self,
        model: str,
        system: str,
        messages: list[Message],
        schema: type[BaseModel] | None,
        max_tokens: int,
        timeout: float,
    ) -> RawResult: ...


def classify_http_error(status: int, headers: dict, body: str) -> LLMError:
    retry_after = None
    try:
        retry_after = float(headers.get("retry-after", ""))
    except ValueError:
        pass
    detail = f"HTTP {status}: {body[:200]}"
    if status == 429 or status >= 500:
        return LLMError(detail, retryable=True, retry_after=retry_after)
    if status in (401, 403):
        return LLMError(detail, auth=True)
    return LLMError(detail)


def extract_json(text: str) -> dict:
    """Parse a JSON object from model text, tolerating code fences and surrounding prose."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(t[start : end + 1])
