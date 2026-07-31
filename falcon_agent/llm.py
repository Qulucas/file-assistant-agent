from __future__ import annotations

import json
import time
from typing import Any

from openai import OpenAI


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        temperature: float = 0.2,
        max_retries: int = 1,
        timeout: float = 120.0,
    ):
        self.model = model
        self.temperature = temperature
        self.max_retries = max_retries
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self.call_count = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def _sanitize(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for m in messages:
            clean = {"role": m["role"]}
            if m.get("content") is not None:
                clean["content"] = m["content"]
            if m.get("tool_calls"):
                clean["tool_calls"] = m["tool_calls"]
            if m.get("tool_call_id"):
                clean["tool_call_id"] = m["tool_call_id"]
            out.append(clean)
        return out

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "messages": self._sanitize(messages),
                    "temperature": self.temperature,
                }
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"
                resp = self.client.chat.completions.create(**kwargs)
                choice = resp.choices[0].message
                if resp.usage:
                    self.prompt_tokens += resp.usage.prompt_tokens or 0
                    self.completion_tokens += resp.usage.completion_tokens or 0
                self.call_count += 1
                return self._parse_message(choice)
            except Exception as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(2 * (attempt + 1))
        raise LLMError(f"LLM call failed after {self.max_retries + 1} attempts: {last_exc}")

    def _parse_message(self, msg: Any) -> dict[str, Any]:
        calls = None
        if getattr(msg, "tool_calls", None):
            calls = []
            for tc in msg.tool_calls:
                raw = tc.function.arguments or ""
                try:
                    arguments = json.loads(raw)
                    parse_error = None
                except json.JSONDecodeError as exc:
                    arguments = raw
                    parse_error = str(exc)
                calls.append(
                    {
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": arguments,
                        "parse_error": parse_error,
                    }
                )
        return {"content": msg.content, "tool_calls": calls}

    def usage(self) -> dict[str, int]:
        return {
            "llm_calls": self.call_count,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }
