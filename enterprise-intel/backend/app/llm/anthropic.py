import httpx
from pydantic import BaseModel

from app.llm.base import LLMError, Message, RawResult, classify_http_error


class ClaudeProvider:
    name = "anthropic"

    def __init__(self, api_key: str):
        self._client = httpx.Client(
            base_url="https://api.anthropic.com",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        )
        # Some models (e.g. Sonnet 5.5) reject a forced tool_choice; once seen, those use "auto" instead.
        self._auto_tool_models: set[str] = set()

    def generate(
        self,
        model: str,
        system: str,
        messages: list[Message],
        schema: type[BaseModel] | None,
        max_tokens: int,
        timeout: float,
    ) -> RawResult:
        body: dict = {
            "model": model,
            "max_tokens": max_tokens,
            # The long, stable system prompt is cached between calls.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
        }
        if schema is not None:
            # Structured output: force a single tool call whose input schema is the Pydantic schema.
            body["tools"] = [
                {"name": "respond", "description": "Return the structured response.", "input_schema": schema.model_json_schema()}
            ]
            if model in self._auto_tool_models:
                self._use_auto_tool_choice(body)
            else:
                body["tool_choice"] = {"type": "tool", "name": "respond"}

        resp = self._post(body, timeout)
        if resp.status_code == 400 and schema is not None and "tool_choice" in resp.text and model not in self._auto_tool_models:
            self._auto_tool_models.add(model)
            self._use_auto_tool_choice(body)
            resp = self._post(body, timeout)
        if resp.status_code != 200:
            raise classify_http_error(resp.status_code, dict(resp.headers), resp.text)

        data = resp.json()
        text = "".join(b.get("text", "") for b in data["content"] if b["type"] == "text")
        tool_input = next((b["input"] for b in data["content"] if b["type"] == "tool_use"), None)
        usage = data.get("usage", {})
        return RawResult(
            text=text,
            json=tool_input,
            input_tokens=usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            finish_reason=data.get("stop_reason"),
        )

    def _post(self, body: dict, timeout: float) -> httpx.Response:
        try:
            return self._client.post("/v1/messages", json=body, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise LLMError("request timed out", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"network error: {exc}", retryable=True) from exc

    @staticmethod
    def _use_auto_tool_choice(body: dict) -> None:
        body["tool_choice"] = {"type": "auto"}
        body["system"][0]["text"] += "\n\nYou must reply by calling the `respond` tool. Do not write any other text."
