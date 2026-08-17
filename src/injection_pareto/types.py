from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, TypeVar


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    role: str
    content: str
    # Present on an assistant turn that requested tool calls, and on the
    # tool-role message answering one — needed to round-trip a full
    # multi-turn tool-calling transcript through `ModelClient.generate`
    # without losing structure (e.g. AgentDojo's agent loop).
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None


@dataclass
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class DefenseContext:
    """Shared, read-mostly context passed to every hook call for one episode."""

    episode_id: str | None = None
    task_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CostRecord:
    usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0

    def __add__(self, other: CostRecord) -> CostRecord:
        return CostRecord(
            usd=self.usd + other.usd,
            tokens_in=self.tokens_in + other.tokens_in,
            tokens_out=self.tokens_out + other.tokens_out,
        )


class Verdict(Enum):
    """Whether a hook's caller (the `DefenseStack`) should keep iterating.

    Only `Defense.on_pre_tool_call` gives `BLOCK` operational meaning today —
    it prevents that tool call from executing. `on_pre_generate` and
    `on_tool_result` blocks are reserved for future defenses (e.g. aborting
    an episode outright), but `DefenseStack` honors the short-circuit
    uniformly across all three hooks.
    """

    ALLOW = "allow"
    BLOCK = "block"


T = TypeVar("T")


@dataclass
class HookResult(Generic[T]):
    value: T
    verdict: Verdict = Verdict.ALLOW
    reason: str | None = None
