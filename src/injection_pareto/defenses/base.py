from __future__ import annotations

from typing import Protocol, runtime_checkable

from injection_pareto.types import (
    CostRecord,
    DefenseContext,
    HookResult,
    Message,
    ToolCall,
    ToolResult,
)


@runtime_checkable
class Defense(Protocol):
    """Contract every defense implements. `DefenseStack` composes these in order.

    Each hook receives the shared `DefenseContext` plus the value it may
    inspect and/or mutate, and returns a `HookResult` wrapping the
    (possibly unchanged) value and a `Verdict`. See `Verdict` for what
    `BLOCK` means for each hook.
    """

    def on_pre_generate(
        self, context: DefenseContext, messages: list[Message]
    ) -> HookResult[list[Message]]:
        """Inspect/mutate the message list before it is sent to the model."""
        ...

    def on_pre_tool_call(
        self, context: DefenseContext, tool_call: ToolCall
    ) -> HookResult[ToolCall]:
        """Inspect/mutate/block a tool call before it executes."""
        ...

    def on_tool_result(
        self, context: DefenseContext, tool_result: ToolResult
    ) -> HookResult[ToolResult]:
        """Inspect/mutate a tool result before it re-enters the transcript."""
        ...

    def cost(self) -> CostRecord:
        """This defense's own accumulated cost (e.g. extra guard-model calls)."""
        ...
