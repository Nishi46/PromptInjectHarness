from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from injection_pareto.clients.base import ModelResponse
from injection_pareto.types import CostRecord, ToolCall


def compute_cache_key(
    *,
    model_id: str,
    messages: list[dict[str, Any]],
    params: dict[str, Any],
    seed: int | None,
) -> str:
    """Content-address a request. Canonical JSON (sorted keys, no whitespace)
    first, so key order in `params`/`messages` never breaks a cache hit."""
    canonical = json.dumps(
        {"model_id": model_id, "messages": messages, "params": params, "seed": seed},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ResponseCache:
    """Disk-backed, content-addressed store of `ModelResponse`s.

    One JSON file per key under `cache_dir`. A faithful store — round-trips
    exactly what was `put()`; it's `CachedModelClient`'s job to decide what
    a hit *means* (e.g. zeroing cost, setting `cache_hit=True`).
    """

    def __init__(self, cache_dir: str | Path = ".cache/responses") -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def get(self, key: str) -> ModelResponse | None:
        path = self._path(key)
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return ModelResponse(
            text=data["text"],
            tool_calls=[ToolCall(**tc) for tc in data["tool_calls"]],
            tokens_in=data["tokens_in"],
            tokens_out=data["tokens_out"],
            wall_ms=data["wall_ms"],
            cost=CostRecord(**data["cost"]),
            raw=data["raw"],
        )

    def put(self, key: str, response: ModelResponse) -> None:
        payload = {
            "text": response.text,
            "tool_calls": [asdict(tc) for tc in response.tool_calls],
            "tokens_in": response.tokens_in,
            "tokens_out": response.tokens_out,
            "wall_ms": response.wall_ms,
            "cost": asdict(response.cost),
            "raw": response.raw,
        }
        self._path(key).write_text(json.dumps(payload))
