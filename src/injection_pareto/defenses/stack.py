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
    the remaining defenses. The final `HookResult`'s `reason` carries the
    last non-`None` reason seen across the stack (whichever verdict it came
    with) -- needed so an ALLOW-verdict defense that still wants to log
    something (e.g. S2-05's `GuardModel`, which logs its score on every
    call, not just blocks) doesn't have that reason silently dropped just
    because nothing blocked. With more than one reason-setting member in the
    same stack (Sprint 6 composition), only the last one survives here --
    fine for today's single-defense-per-hook-mutation sweeps; revisit if
    composed defenses need every member's reason preserved.
    """

    def __init__(self, defenses: list[Defense]) -> None:
        self.defenses = defenses

    def on_pre_generate(
        self, context: DefenseContext, messages: list[Message]
    ) -> HookResult[list[Message]]:
        current = messages
        last_reason: str | None = None
        for defense in self.defenses:
            result = defense.on_pre_generate(context, current)
            current = result.value
            if result.reason is not None:
                last_reason = result.reason
            if result.verdict is Verdict.BLOCK:
                return HookResult(value=current, verdict=Verdict.BLOCK, reason=result.reason)
        return HookResult(value=current, reason=last_reason)

    def on_pre_tool_call(
        self, context: DefenseContext, tool_call: ToolCall
    ) -> HookResult[ToolCall]:
        current = tool_call
        last_reason: str | None = None
        for defense in self.defenses:
            result = defense.on_pre_tool_call(context, current)
            current = result.value
            if result.reason is not None:
                last_reason = result.reason
            if result.verdict is Verdict.BLOCK:
                return HookResult(value=current, verdict=Verdict.BLOCK, reason=result.reason)
        return HookResult(value=current, reason=last_reason)

    def on_tool_result(
        self, context: DefenseContext, tool_result: ToolResult
    ) -> HookResult[ToolResult]:
        current = tool_result
        last_reason: str | None = None
        for defense in self.defenses:
            result = defense.on_tool_result(context, current)
            current = result.value
            if result.reason is not None:
                last_reason = result.reason
            if result.verdict is Verdict.BLOCK:
                return HookResult(value=current, verdict=Verdict.BLOCK, reason=result.reason)
        return HookResult(value=current, reason=last_reason)

    def cost(self) -> CostRecord:
        total = CostRecord()
        for defense in self.defenses:
            total = total + defense.cost()
        return total
