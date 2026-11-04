import json

import httpx
from pydantic import BaseModel

from app.llm.base import LLMError, Message, RawResult, classify_http_error


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str):
        self._client = httpx.Client(
            base_url="https://generativelanguage.googleapis.com/v1beta", headers={"x-goog-api-key": api_key}
        )

    def generate(
        self,
        model: str,
        system: str,
        messages: list[Message],
        schema: type[BaseModel] | None,
        max_tokens: int,
        timeout: float,
    ) -> RawResult:
        if schema is not None:
            system += "\n\nRespond ONLY with a JSON object that matches this JSON schema:\n" + json.dumps(
                schema.model_json_schema()
            )
        config: dict = {"maxOutputTokens": max_tokens, "temperature": 0, "thinkingConfig": {"thinkingBudget": 0}}
        if schema is not None:
            config["responseMimeType"] = "application/json"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [
                {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                for m in messages
            ],
            "generationConfig": config,
        }

        try:
            resp = self._client.post(f"/models/{model}:generateContent", json=body, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise LLMError("request timed out", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"network error: {exc}", retryable=True) from exc
        if resp.status_code != 200:
            raise classify_http_error(resp.status_code, dict(resp.headers), resp.text)

        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates or "content" not in candidates[0]:
            raise LLMError(f"empty response ({data.get('promptFeedback', 'no candidates')})")
        text = "".join(p.get("text", "") for p in candidates[0]["content"].get("parts", []))
        usage = data.get("usageMetadata", {})
        return RawResult(
            text=text,
            json=None,
            input_tokens=usage.get("promptTokenCount", 0),
            output_tokens=usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0),
            finish_reason=candidates[0].get("finishReason"),
        )
