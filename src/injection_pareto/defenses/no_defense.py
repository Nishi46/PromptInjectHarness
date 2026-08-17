from __future__ import annotations

from injection_pareto.types import (
    CostRecord,
    DefenseContext,
    HookResult,
    Message,
    ToolCall,
    ToolResult,
)


class NoDefense:
    """Pure pass-through. Proves the `Defense` protocol adds no side effects."""

    def on_pre_generate(
        self, context: DefenseContext, messages: list[Message]
    ) -> HookResult[list[Message]]:
        return HookResult(value=messages)

    def on_pre_tool_call(
        self, context: DefenseContext, tool_call: ToolCall
    ) -> HookResult[ToolCall]:
        return HookResult(value=tool_call)

    def on_tool_result(
        self, context: DefenseContext, tool_result: ToolResult
    ) -> HookResult[ToolResult]:
        return HookResult(value=tool_result)

    def cost(self) -> CostRecord:
        return CostRecord()
