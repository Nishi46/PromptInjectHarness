from __future__ import annotations

from typing import Any

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
    because nothing blocked.

    Constructed from `(name, Defense)` pairs (S6-01) rather than bare
    `Defense` instances -- a composed stack (`resolve_defense_stack`,
    `defenses/registry.py`) needs each member's own registered name to
    attribute a `defense_event`/`cost_record` row to the right layer; the
    smaller, more additive place to carry that name is here, alongside the
    instance, rather than as a new attribute on the `Defense` protocol
    itself (which every one of the 8 concrete defense classes would then
    need). `.defenses` stays a plain `list[Defense]` (derived from
    `named_defenses`) for any caller that only ever needed instances, e.g.
    `cost()` below.
    """

    def __init__(self, defenses: list[tuple[str, Defense]]) -> None:
        self.named_defenses = defenses
        self.defenses = [defense for _, defense in defenses]
        # Populated fresh at the start of every `on_pre_generate`/
        # `on_pre_tool_call`/`on_tool_result` call, below, with every member
        # that actually ran this call (name, that member's own `HookResult`)
        # in call order -- a member skipped because an earlier one already
        # returned `BLOCK` is simply absent, never fabricated. Read by the
        # adapters immediately after each hook call for S6-01's per-layer
        # `defense_event`/`cost_record` attribution; `cost()` doesn't need
        # this (it reads `named_defenses`/`defenses` directly).
        self.last_member_results: dict[str, list[tuple[str, HookResult[Any]]]] = {
            "on_pre_generate": [],
            "on_pre_tool_call": [],
            "on_tool_result": [],
        }

    def on_pre_generate(
        self, context: DefenseContext, messages: list[Message]
    ) -> HookResult[list[Message]]:
        current = messages
        last_reason: str | None = None
        member_results: list[tuple[str, HookResult[Any]]] = []
        for name, defense in self.named_defenses:
            result = defense.on_pre_generate(context, current)
            member_results.append((name, result))
            current = result.value
            if result.reason is not None:
                last_reason = result.reason
            if result.verdict is Verdict.BLOCK:
                self.last_member_results["on_pre_generate"] = member_results
                return HookResult(value=current, verdict=Verdict.BLOCK, reason=result.reason)
        self.last_member_results["on_pre_generate"] = member_results
        return HookResult(value=current, reason=last_reason)

    def on_pre_tool_call(
        self, context: DefenseContext, tool_call: ToolCall
    ) -> HookResult[ToolCall]:
        current = tool_call
        last_reason: str | None = None
        member_results: list[tuple[str, HookResult[Any]]] = []
        for name, defense in self.named_defenses:
            result = defense.on_pre_tool_call(context, current)
            member_results.append((name, result))
            current = result.value
            if result.reason is not None:
                last_reason = result.reason
            if result.verdict is Verdict.BLOCK:
                self.last_member_results["on_pre_tool_call"] = member_results
                return HookResult(value=current, verdict=Verdict.BLOCK, reason=result.reason)
        self.last_member_results["on_pre_tool_call"] = member_results
        return HookResult(value=current, reason=last_reason)

    def on_tool_result(
        self, context: DefenseContext, tool_result: ToolResult
    ) -> HookResult[ToolResult]:
        current = tool_result
        last_reason: str | None = None
        member_results: list[tuple[str, HookResult[Any]]] = []
        for name, defense in self.named_defenses:
            result = defense.on_tool_result(context, current)
            member_results.append((name, result))
            current = result.value
            if result.reason is not None:
                last_reason = result.reason
            if result.verdict is Verdict.BLOCK:
                self.last_member_results["on_tool_result"] = member_results
                return HookResult(value=current, verdict=Verdict.BLOCK, reason=result.reason)
        self.last_member_results["on_tool_result"] = member_results
        return HookResult(value=current, reason=last_reason)

    def cost(self) -> CostRecord:
        total = CostRecord()
        for defense in self.defenses:
            total = total + defense.cost()
        return total
