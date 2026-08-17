from __future__ import annotations

import time
from typing import Any

import requests

from injection_pareto.clients.base import ModelRequest, ModelResponse
from injection_pareto.clients.costs import compute_cost
from injection_pareto.types import Message, ToolCall


def _serialize_message(message: Message) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        payload["tool_calls"] = [
            {"function": {"name": tc.name, "arguments": tc.arguments}} for tc in message.tool_calls
        ]
    return payload


class OllamaClient:
    """Local inference via Ollama's HTTP API. Covers tiers L1–L3."""

    def __init__(
        self,
        model: str,
        *,
        digest: str | None = None,
        base_url: str = "http://localhost:11434",
        session: requests.Session | None = None,
        timeout_s: float = 300.0,
    ) -> None:
        self.model = model
        self.digest = digest
        # Ollama tags get republished (Appendix A.1), so the cache key must
        # pin the digest when one's known — otherwise a re-tagged model
        # would silently serve stale cached responses.
        self.cache_model_id = f"ollama:{model}@{digest}" if digest else f"ollama:{model}"
        self.base_url = base_url.rstrip("/")
        self._session = session or requests.Session()
        self._timeout_s = timeout_s

    def generate(self, request: ModelRequest) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [_serialize_message(m) for m in request.messages],
            "stream": False,
        }
        if request.tools:
            payload["tools"] = request.tools

        options: dict[str, Any] = dict(request.params)
        if request.seed is not None:
            options["seed"] = request.seed
        if options:
            payload["options"] = options

        start = time.perf_counter()
        response = self._session.post(
            f"{self.base_url}/api/chat", json=payload, timeout=self._timeout_s
        )
        response.raise_for_status()
        wall_ms = int((time.perf_counter() - start) * 1000)

        data = response.json()
        message = data.get("message", {})
        tool_calls = [
            ToolCall(
                id=str(i),
                name=tc["function"]["name"],
                arguments=tc["function"].get("arguments", {}),
            )
            for i, tc in enumerate(message.get("tool_calls") or [])
        ]

        tokens_in = int(data.get("prompt_eval_count", 0))
        tokens_out = int(data.get("eval_count", 0))
        cost = compute_cost(
            provider="ollama", model=self.model, tokens_in=tokens_in, tokens_out=tokens_out
        )

        return ModelResponse(
            text=message.get("content", ""),
            tool_calls=tool_calls,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            wall_ms=wall_ms,
            cost=cost,
            raw=data,
        )
