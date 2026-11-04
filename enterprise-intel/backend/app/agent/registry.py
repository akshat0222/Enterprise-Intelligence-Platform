import json
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from app.llm.base import CallContext
from app.llm.gateway import LLMGateway


class ToolError(Exception):
    """A tool failure the planner can see and recover from."""


class ToolArgsError(ToolError):
    """Arguments failed validation; the planner gets one chance to repair them."""


@dataclass
class ToolContext:
    """Server-side context for a tool call. user_id comes from the JWT, never from LLM-generated arguments."""

    user_id: uuid.UUID
    gateway: LLMGateway
    ctx: CallContext
    state: dict  # snapshot: sources, analysis, doc_scope, question


@dataclass
class ToolResult:
    summary: dict
    update: dict = field(default_factory=dict)  # merged into the agent state
    insufficient: bool = False


@dataclass
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    fn: Callable[[BaseModel, ToolContext], ToolResult]
    timeout_s: float


REGISTRY: dict[str, ToolSpec] = {}
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="tool")


def register_tool(name: str, args_model: type[BaseModel], description: str, timeout_s: float = 30):
    """Adding a tool = one decorated function; the graph does not change."""

    def decorator(fn):
        REGISTRY[name] = ToolSpec(name, description, args_model, fn, timeout_s)
        return fn

    return decorator


def describe_tools() -> str:
    lines = []
    for spec in REGISTRY.values():
        schema = spec.args_model.model_json_schema()
        props = {k: v.get("type", v.get("anyOf", "any")) for k, v in schema.get("properties", {}).items()}
        lines.append(f"- {spec.name}: {spec.description}\n  args: {json.dumps(props)} required: {schema.get('required', [])}")
    return "\n".join(lines)


def run_tool(name: str, raw_args: dict, tctx: ToolContext) -> ToolResult:
    spec = REGISTRY.get(name)
    if spec is None:
        raise ToolArgsError(f"unknown tool '{name}'. Available: {', '.join(REGISTRY)}")
    try:
        args = spec.args_model.model_validate(raw_args or {})
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise ToolArgsError(f"invalid arguments for {name}: {problems}") from None

    future = _pool.submit(spec.fn, args, tctx)
    try:
        return future.result(timeout=spec.timeout_s)
    except FutureTimeout:
        raise ToolError(f"{name} timed out after {spec.timeout_s}s") from None
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"{name} failed: {type(exc).__name__}: {str(exc)[:200]}") from exc
