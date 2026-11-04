import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from app.config import settings
from app.db.models import LLMCall
from app.db.session import SessionLocal
from app.llm.anthropic import ClaudeProvider
from app.llm.base import (
    CallContext,
    LLMError,
    LLMProvider,
    LLMResult,
    LLMUnavailable,
    Message,
    RawResult,
    extract_json,
)
from app.llm.gemini import GeminiProvider
from app.llm.pricing import cost_usd
from app.observability import get_langfuse

logger = logging.getLogger("app.llm")


@dataclass(frozen=True)
class Route:
    provider: str
    model: str


@dataclass
class CallRecord:
    ctx: CallContext
    task: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    success: bool
    error: str | None
    fallback_used: bool


def db_recorder(rec: CallRecord) -> None:
    try:
        with SessionLocal() as db:
            db.add(
                LLMCall(
                    user_id=rec.ctx.user_id,
                    conversation_id=rec.ctx.conversation_id,
                    message_id=rec.ctx.message_id,
                    task=rec.task,
                    provider=rec.provider,
                    model=rec.model,
                    input_tokens=rec.input_tokens,
                    output_tokens=rec.output_tokens,
                    cost_usd=rec.cost_usd,
                    latency_ms=rec.latency_ms,
                    success=rec.success,
                    error=rec.error,
                    fallback_used=rec.fallback_used,
                )
            )
            db.commit()
    except Exception:
        logger.exception("failed to record llm call")


class LLMGateway:
    """The only place the app talks to LLMs: routing by task, retries, circuit breaker, fallback, validation, logging."""

    def __init__(
        self,
        providers: dict[str, LLMProvider],
        routes: dict[str, list[Route]],
        recorder: Callable[[CallRecord], None] = db_recorder,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int | None = None,
    ):
        self.providers = providers
        self.routes = routes
        self.recorder = recorder
        self.sleep = sleep
        self.max_retries = settings.llm_max_retries if max_retries is None else max_retries
        self._failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    # --- routing -----------------------------------------------------------------------------------------------
    def available_models(self) -> list[dict]:
        seen: dict[str, str] = {}
        for routes in self.routes.values():
            for r in routes:
                if r.provider in self.providers:
                    seen.setdefault(r.model, r.provider)
        return [{"model": m, "provider": p} for m, p in seen.items()]

    def _route(self, task: str, ctx: CallContext) -> list[Route]:
        routes = [r for r in self.routes.get(task, []) if r.provider in self.providers]
        if ctx.preferred_model:
            known = {m["model"]: m["provider"] for m in self.available_models()}
            if ctx.preferred_model in known:
                preferred = Route(known[ctx.preferred_model], ctx.preferred_model)
                routes = [preferred] + [r for r in routes if r != preferred]
        return routes

    # --- circuit breaker -----------------------------------------------------------------------------------------
    def _circuit_open(self, provider: str) -> bool:
        return time.monotonic() < self._open_until.get(provider, 0)

    def _note_failure(self, provider: str) -> None:
        self._failures[provider] = self._failures.get(provider, 0) + 1
        if self._failures[provider] >= settings.circuit_threshold:
            self._open_until[provider] = time.monotonic() + settings.circuit_cooldown_s
            self._failures[provider] = 0
            logger.warning("circuit opened for %s", provider)

    def _note_success(self, provider: str) -> None:
        self._failures[provider] = 0

    # --- public API ----------------------------------------------------------------------------------------------
    def generate(
        self,
        task: str,
        system: str,
        messages: list[Message],
        schema: type[BaseModel] | None = None,
        ctx: CallContext | None = None,
        max_tokens: int = 2000,
    ) -> LLMResult:
        ctx = ctx or CallContext()
        routes = self._route(task, ctx)
        if not routes:
            raise LLMUnavailable("no LLM providers configured")

        errors: list[str] = []
        for index, route in enumerate(routes):
            if self._circuit_open(route.provider):
                errors.append(f"{route.provider}: circuit open")
                continue
            try:
                return self._call_route(task, route, system, messages, schema, ctx, max_tokens, fallback=index > 0)
            except LLMError as exc:
                errors.append(f"{route.provider}/{route.model}: {exc}")
                logger.warning("provider failed task=%s %s/%s: %s", task, route.provider, route.model, exc)
        raise LLMUnavailable("; ".join(errors))

    # --- internals ------------------------------------------------------------------------------------------------
    def _call_route(self, task, route, system, messages, schema, ctx, max_tokens, fallback) -> LLMResult:
        provider = self.providers[route.provider]
        convo = list(messages)
        repaired = False
        attempt = 0
        while True:
            start_ns, start = time.time_ns(), time.perf_counter()
            try:
                raw = provider.generate(route.model, system, convo, schema, max_tokens, settings.llm_timeout_s)
            except LLMError as exc:
                self._finish(ctx, task, route, system, convo, None, 0, 0, start, start_ns, fallback, str(exc))
                self._note_failure(route.provider)
                if exc.retryable and attempt < self.max_retries:
                    self.sleep(exc.retry_after or min(2**attempt + random.random(), 15))
                    attempt += 1
                    continue
                raise

            parsed, problem = self._parse(raw, schema)
            self._finish(ctx, task, route, system, convo, raw, raw.input_tokens, raw.output_tokens, start, start_ns,
                         fallback, problem)  # fmt: skip
            if problem is None:
                self._note_success(route.provider)
                latency = round((time.perf_counter() - start) * 1000)
                cost = cost_usd(route.model, raw.input_tokens, raw.output_tokens)
                return LLMResult(raw.text, parsed, raw.input_tokens, raw.output_tokens, route.model, route.provider,
                                 latency, raw.finish_reason, fallback, cost)  # fmt: skip
            if repaired:
                raise LLMError(f"invalid structured output after repair: {problem}")
            # One repair retry: show the model its own reply and the validation error.
            repaired = True
            convo = convo + [
                {"role": "assistant", "content": raw.text or str(raw.json)},
                {"role": "user", "content": f"Your reply failed validation: {problem}\nReturn corrected output only."},
            ]

    @staticmethod
    def _parse(raw: RawResult, schema: type[BaseModel] | None) -> tuple[BaseModel | None, str | None]:
        if schema is None:
            return (None, None) if raw.text.strip() else (None, "empty response")
        try:
            data = raw.json if raw.json is not None else extract_json(raw.text)
            return schema.model_validate(data), None
        except ValueError as exc:
            return None, str(exc)[:500]

    def _finish(self, ctx, task, route, system, convo, raw, in_tok, out_tok, start, start_ns, fallback, error) -> None:
        latency = round((time.perf_counter() - start) * 1000)
        cost = cost_usd(route.model, in_tok, out_tok)
        ctx.calls += 1
        ctx.input_tokens += in_tok
        ctx.output_tokens += out_tok
        ctx.cost_usd += cost
        ctx.fallback_used = ctx.fallback_used or fallback
        if error is None:
            ctx.models.add(route.model)
        self.recorder(CallRecord(ctx, task, route.provider, route.model, in_tok, out_tok, cost, latency,
                                 error is None, error, fallback))  # fmt: skip
        if ctx.trace_id:
            get_langfuse().generation(
                trace_id=ctx.trace_id, name=task, model=route.model,
                input=[{"role": "system", "content": system[:1500]}, *convo[-2:]],
                output=(raw.text or raw.json) if raw else None, input_tokens=in_tok, output_tokens=out_tok,
                cost_usd=cost, start_ns=start_ns, user_id=ctx.user_id, session_id=ctx.conversation_id, error=error,
            )  # fmt: skip


def default_routes() -> dict[str, list[Route]]:
    fast = Route("anthropic", settings.anthropic_fast_model)
    smart = Route("anthropic", settings.anthropic_smart_model)
    gemini = Route("gemini", settings.gemini_model)
    cheap = [fast, gemini]
    strong = [smart, gemini]
    return {
        "router": cheap, "rewrite": cheap, "judge": cheap, "summary": cheap, "direct": cheap, "plan": cheap,
        "answer": strong, "analysis": strong, "report": strong,
    }  # fmt: skip


def build_gateway() -> LLMGateway:
    providers: dict[str, LLMProvider] = {}
    if settings.anthropic_api_key:
        providers["anthropic"] = ClaudeProvider(settings.anthropic_api_key)
    if settings.gemini_api_key:
        providers["gemini"] = GeminiProvider(settings.gemini_api_key)
    return LLMGateway(providers, default_routes())


_gateway: LLMGateway | None = None


def get_gateway() -> LLMGateway:
    global _gateway
    if _gateway is None:
        _gateway = build_gateway()
    return _gateway


def set_gateway(gateway: LLMGateway | None) -> None:
    """Swap the gateway (tests inject fake providers here)."""
    global _gateway
    _gateway = gateway

