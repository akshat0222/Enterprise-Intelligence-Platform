"""Langfuse tracing over OpenTelemetry (OTLP/HTTP JSON). Best-effort: tracing never breaks a request.

Langfuse's older ingestion API is being shut down, so spans are sent to /api/public/otel/v1/traces using Langfuse's
attribute conventions: one root span per chat turn, a "generation" span per LLM call, a "tool" span per agent step.
"""

import base64
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import httpx

from app.config import settings

logger = logging.getLogger("app.langfuse")
MAX_FIELD_CHARS = 4000
OK, ERROR = 1, 2  # OTLP status codes


def root_span_id(trace_id: str) -> str:
    """The root span id is derived from the trace id, so child spans can be sent before the root finishes."""
    return trace_id[:16]


def _clip(value) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text[:MAX_FIELD_CHARS] + ("…" if len(text) > MAX_FIELD_CHARS else "")


def _attr(key: str, value) -> dict | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    return {"key": key, "value": {"stringValue": str(value)}}


class Langfuse:
    def __init__(self, public_key: str, secret_key: str, host: str):
        self.enabled = bool(public_key and secret_key)
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {token}",
            "x-langfuse-ingestion-version": "4",  # without it, data can take minutes to appear
            "Content-Type": "application/json",
        }
        self._url = host.rstrip("/") + "/api/public/otel/v1/traces"
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="langfuse")

    def _send(self, span: dict) -> None:
        body = {
            "resourceSpans": [{
                "resource": {"attributes": [_attr("service.name", "enterprise-intel")]},
                "scopeSpans": [{"scope": {"name": "enterprise-intel"}, "spans": [span]}],
            }]
        }  # fmt: skip
        try:
            resp = httpx.post(self._url, headers=self._headers, json=body, timeout=10)
            if resp.status_code >= 300:
                logger.warning("langfuse otel export failed: %s %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.warning("langfuse otel export error: %s", exc)

    def _span(self, *, trace_id: str, span_id: str, parent: str | None, name: str, start_ns: int, end_ns: int,
              obs_type: str, user_id, session_id, trace_name: str, attrs: dict, error: str | None = None) -> None:  # fmt: skip
        if not self.enabled:
            return
        base = {
            "langfuse.trace.name": trace_name,
            "langfuse.user.id": str(user_id) if user_id else None,
            "langfuse.session.id": str(session_id) if session_id else None,
            "langfuse.observation.type": obs_type,
        }
        span = {
            "traceId": trace_id, "spanId": span_id, "name": name, "kind": 1,
            "startTimeUnixNano": str(start_ns), "endTimeUnixNano": str(end_ns),
            "attributes": [a for a in (_attr(k, v) for k, v in {**base, **attrs}.items()) if a],
            "status": {"code": ERROR if error else OK, "message": error or ""},
        }  # fmt: skip
        if parent:
            span["parentSpanId"] = parent
        self._pool.submit(self._send, span)

    def root(self, trace_id: str, name: str, user_id, session_id, start_ns: int, input, output, metadata: dict,  # noqa: A002
             error: str | None = None) -> None:
        attrs = {
            "langfuse.observation.input": _clip(input),
            "langfuse.observation.output": _clip(output),
            "langfuse.trace.input": _clip(input),
            "langfuse.trace.output": _clip(output),
        }
        for key, value in metadata.items():
            attrs[f"langfuse.trace.metadata.{key}"] = _clip(value)
        self._span(trace_id=trace_id, span_id=root_span_id(trace_id), parent=None, name=name, start_ns=start_ns,
                   end_ns=time.time_ns(), obs_type="span", user_id=user_id, session_id=session_id, trace_name=name,
                   attrs=attrs, error=error)  # fmt: skip

    def generation(self, *, trace_id: str, name: str, model: str, input, output, input_tokens: int,  # noqa: A002
                   output_tokens: int, cost_usd: float, start_ns: int, user_id, session_id,
                   error: str | None = None) -> None:  # fmt: skip
        attrs = {
            "langfuse.observation.input": _clip(input),
            "langfuse.observation.output": _clip(output),
            "langfuse.observation.model.name": model,
            "langfuse.observation.usage_details": json.dumps({"input": input_tokens, "output": output_tokens}),
            "langfuse.observation.cost_details": json.dumps({"total": cost_usd}),
            "langfuse.observation.level": "ERROR" if error else "DEFAULT",
            "langfuse.observation.status_message": error,
        }
        self._span(trace_id=trace_id, span_id=_new_span_id(), parent=root_span_id(trace_id), name=name,
                   start_ns=start_ns, end_ns=time.time_ns(), obs_type="generation", user_id=user_id,
                   session_id=session_id, trace_name="chat_turn", attrs=attrs, error=error)  # fmt: skip

    def tool(self, *, trace_id: str, name: str, input, output, latency_ms: int, user_id, session_id,  # noqa: A002
             error: str | None = None) -> None:
        end = time.time_ns()
        attrs = {"langfuse.observation.input": _clip(input), "langfuse.observation.output": _clip(output)}
        self._span(trace_id=trace_id, span_id=_new_span_id(), parent=root_span_id(trace_id), name=name,
                   start_ns=end - latency_ms * 1_000_000, end_ns=end, obs_type="tool", user_id=user_id,
                   session_id=session_id, trace_name="chat_turn", attrs=attrs, error=error)  # fmt: skip

    def flush(self) -> None:
        self._pool.submit(lambda: None).result(timeout=20)


def _new_span_id() -> str:
    import secrets

    return secrets.token_hex(8)


@lru_cache
def get_langfuse() -> Langfuse:
    return Langfuse(settings.langfuse_public_key, settings.langfuse_secret_key, settings.langfuse_host)
