from __future__ import annotations

from injection_pareto.defenses.base import Defense
from injection_pareto.types import (
    CostRecord,
    DefenseContext,
    HookResult,
    Message,
    ToolCall,
    ToolResult,
    Verdict,
)


class DefenseStack:
    """Composes an ordered list of `Defense`s behind the same 4-hook interface.

    Each hook threads its value through the stack in order; a `BLOCK`
    verdict from any member stops the stack immediately without calling
    the remaining defenses.
    """

    def __init__(self, defenses: list[Defense]) -> None:
        self.defenses = defenses

    def on_pre_generate(
        self, context: DefenseContext, messages: list[Message]
    ) -> HookResult[list[Message]]:
        current = messages
        for defense in self.defenses:
            result = defense.on_pre_generate(context, current)
            current = result.value
            if result.verdict is Verdict.BLOCK:
                return HookResult(value=current, verdict=Verdict.BLOCK, reason=result.reason)
        return HookResult(value=current)

    def on_pre_tool_call(
        self, context: DefenseContext, tool_call: ToolCall
    ) -> HookResult[ToolCall]:
        current = tool_call
        for defense in self.defenses:
            result = defense.on_pre_tool_call(context, current)
            current = result.value
            if result.verdict is Verdict.BLOCK:
                return HookResult(value=current, verdict=Verdict.BLOCK, reason=result.reason)
        return HookResult(value=current)

    def on_tool_result(
        self, context: DefenseContext, tool_result: ToolResult
    ) -> HookResult[ToolResult]:
        current = tool_result
        for defense in self.defenses:
            result = defense.on_tool_result(context, current)
            current = result.value
            if result.verdict is Verdict.BLOCK:
                return HookResult(value=current, verdict=Verdict.BLOCK, reason=result.reason)
        return HookResult(value=current)

    def cost(self) -> CostRecord:
        total = CostRecord()
        for defense in self.defenses:
            total = total + defense.cost()
        return total
