from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from injection_pareto.types import CostRecord, Message, ToolCall


@dataclass
class ModelRequest:
    messages: list[Message]
    tools: list[dict[str, Any]] | None = None
    params: dict[str, Any] = field(default_factory=dict)
    seed: int | None = None


@dataclass
class ModelResponse:
    text: str
    tool_calls: list[ToolCall]
    tokens_in: int
    tokens_out: int
    wall_ms: int
    cost: CostRecord
    raw: dict[str, Any]


class ModelClient(Protocol):
    """Uniform interface across vendors.

    Each implementation owns request/response translation for its provider,
    times the call itself, and computes `cost` (via `clients.costs.compute_cost`)
    before returning. This layer has no knowledge of the trace schema —
    persisting the returned `CostRecord` to `cost_record` is the caller's job
    (e.g. the AgentDojo adapter in S1-07, which owns `run_id`/`episode_id`).
    """

    def generate(self, request: ModelRequest) -> ModelResponse: ...
