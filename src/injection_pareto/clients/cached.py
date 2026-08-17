from __future__ import annotations

from dataclasses import replace
from typing import Any

from injection_pareto.cache.store import ResponseCache, compute_cache_key
from injection_pareto.clients.base import ModelClient, ModelRequest, ModelResponse
from injection_pareto.types import CostRecord, Message


def _message_to_cache_dict(message: Message) -> dict[str, Any]:
    """Full request-shaping fields only — enough to distinguish requests
    that must never share a cache entry. An assistant turn that issues a
    tool call typically has empty `content`, so `tool_calls`/`tool_call_id`
    must be part of the key or two turns that differ only in which tool
    was called (or its arguments) would hash identically."""
    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        payload["tool_calls"] = [
            {"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in message.tool_calls
        ]
    if message.tool_call_id is not None:
        payload["tool_call_id"] = message.tool_call_id
    return payload


class CachedModelClient:
    """Wraps any `ModelClient` with a content-addressed disk cache.

    `no_cache=True` (the `--no-cache` escape hatch) skips both the read
    *and* the write for that call — not just the read. Letting `--no-cache`
    write would mean a debug flag silently overwrites a previously cached,
    reproducible result with a fresh nondeterministic one; this project's
    reproducibility story (digest-pinned models, cached responses) depends
    on cache entries only ever changing on purpose.
    """

    def __init__(
        self, client: ModelClient, cache: ResponseCache, *, no_cache: bool = False
    ) -> None:
        self._client = client
        self._cache = cache
        self.no_cache = no_cache
        self.cache_model_id = client.cache_model_id

    def generate(self, request: ModelRequest) -> ModelResponse:
        key = compute_cache_key(
            model_id=self._client.cache_model_id,
            messages=[_message_to_cache_dict(m) for m in request.messages],
            params=request.params,
            seed=request.seed,
            tools=request.tools,
        )

        if not self.no_cache:
            cached = self._cache.get(key)
            if cached is not None:
                return replace(
                    cached,
                    cost=CostRecord(
                        usd=0.0,
                        tokens_in=cached.cost.tokens_in,
                        tokens_out=cached.cost.tokens_out,
                    ),
                    cache_hit=True,
                )

        response = self._client.generate(request)
        if not self.no_cache:
            self._cache.put(key, response)
        return response
