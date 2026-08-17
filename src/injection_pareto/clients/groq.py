from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

from injection_pareto.clients.base import ModelRequest, ModelResponse
from injection_pareto.clients.costs import compute_cost
from injection_pareto.types import ToolCall


class GroqClient:
    """Hosted inference via Groq's OpenAI-compatible REST API. Covers tier L5."""

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str = "https://api.groq.com/openai/v1",
        session: requests.Session | None = None,
        timeout_s: float = 300.0,
    ) -> None:
        self.model = model
        self.cache_model_id = f"groq:{model}"
        self.api_key = api_key or os.environ["GROQ_API_KEY"]
        self.base_url = base_url.rstrip("/")
        self._session = session or requests.Session()
        self._timeout_s = timeout_s

    def generate(self, request: ModelRequest) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            **request.params,
        }
        if request.tools:
            payload["tools"] = request.tools
        if request.seed is not None:
            payload["seed"] = request.seed

        start = time.perf_counter()
        response = self._session.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self._timeout_s,
        )
        response.raise_for_status()
        wall_ms = int((time.perf_counter() - start) * 1000)

        data = response.json()
        message = data["choices"][0]["message"]
        tool_calls = [
            ToolCall(
                id=tc.get("id", str(i)),
                name=tc["function"]["name"],
                arguments=json.loads(tc["function"].get("arguments") or "{}"),
            )
            for i, tc in enumerate(message.get("tool_calls") or [])
        ]

        usage = data.get("usage", {})
        tokens_in = int(usage.get("prompt_tokens", 0))
        tokens_out = int(usage.get("completion_tokens", 0))
        cost = compute_cost(
            provider="groq", model=self.model, tokens_in=tokens_in, tokens_out=tokens_out
        )

        return ModelResponse(
            text=message.get("content") or "",
            tool_calls=tool_calls,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            wall_ms=wall_ms,
            cost=cost,
            raw=data,
        )
