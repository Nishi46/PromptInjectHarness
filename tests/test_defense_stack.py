import pytest

from injection_pareto.defenses import DefenseStack, resolve_defense_stack
from injection_pareto.defenses.registry import resolve_defense
from injection_pareto.types import (
    CostRecord,
    DefenseContext,
    HookResult,
    Message,
    ToolCall,
    ToolResult,
    Verdict,
)


class RecordingDefense:
    """No-op defense that appends its name to a shared list on every hook call."""

    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls

    def on_pre_generate(
        self, context: DefenseContext, messages: list[Message]
    ) -> HookResult[list[Message]]:
        self.calls.append(self.name)
        return HookResult(value=messages)

    def on_pre_tool_call(
        self, context: DefenseContext, tool_call: ToolCall
    ) -> HookResult[ToolCall]:
        self.calls.append(self.name)
        return HookResult(value=tool_call)

    def on_tool_result(
        self, context: DefenseContext, tool_result: ToolResult
    ) -> HookResult[ToolResult]:
        self.calls.append(self.name)
        return HookResult(value=tool_result)

    def cost(self) -> CostRecord:
        return CostRecord()


class FixedCostDefense:
    """No-op defense that reports a fixed cost."""

    def __init__(self, cost_record: CostRecord) -> None:
        self._cost = cost_record

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
        return self._cost


class BlockingToolCallDefense:
    """Denies every tool call."""

    def on_pre_generate(
        self, context: DefenseContext, messages: list[Message]
    ) -> HookResult[list[Message]]:
        return HookResult(value=messages)

    def on_pre_tool_call(
        self, context: DefenseContext, tool_call: ToolCall
    ) -> HookResult[ToolCall]:
        return HookResult(value=tool_call, verdict=Verdict.BLOCK, reason="denied")

    def on_tool_result(
        self, context: DefenseContext, tool_result: ToolResult
    ) -> HookResult[ToolResult]:
        return HookResult(value=tool_result)

    def cost(self) -> CostRecord:
        return CostRecord()


class AllowWithReasonDefense:
    """Sets a reason without ever blocking -- e.g. S2-05's `GuardModel`,
    which logs its score on every call, not just when it blocks."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def on_pre_generate(
        self, context: DefenseContext, messages: list[Message]
    ) -> HookResult[list[Message]]:
        return HookResult(value=messages, reason=self._reason)

    def on_pre_tool_call(
        self, context: DefenseContext, tool_call: ToolCall
    ) -> HookResult[ToolCall]:
        return HookResult(value=tool_call, reason=self._reason)

    def on_tool_result(
        self, context: DefenseContext, tool_result: ToolResult
    ) -> HookResult[ToolResult]:
        return HookResult(value=tool_result, reason=self._reason)

    def cost(self) -> CostRecord:
        return CostRecord()


class FlaggingDefense:
    """No-op defense that records whether it was invoked."""

    def __init__(self) -> None:
        self.called = False

    def on_pre_generate(
        self, context: DefenseContext, messages: list[Message]
    ) -> HookResult[list[Message]]:
        return HookResult(value=messages)

    def on_pre_tool_call(
        self, context: DefenseContext, tool_call: ToolCall
    ) -> HookResult[ToolCall]:
        self.called = True
        return HookResult(value=tool_call)

    def on_tool_result(
        self, context: DefenseContext, tool_result: ToolResult
    ) -> HookResult[ToolResult]:
        return HookResult(value=tool_result)

    def cost(self) -> CostRecord:
        return CostRecord()


def test_hook_call_order_matches_stack_order() -> None:
    calls: list[str] = []
    stack = DefenseStack(
        [
            ("first", RecordingDefense("first", calls)),
            ("second", RecordingDefense("second", calls)),
        ]
    )

    stack.on_pre_generate(DefenseContext(), [Message(role="user", content="hi")])

    assert calls == ["first", "second"]


def test_cost_sums_across_stack() -> None:
    stack = DefenseStack(
        [
            ("a", FixedCostDefense(CostRecord(usd=1.5, tokens_in=10, tokens_out=5))),
            ("b", FixedCostDefense(CostRecord(usd=2.5, tokens_in=20, tokens_out=8))),
            ("c", FixedCostDefense(CostRecord(usd=3.0, tokens_in=30, tokens_out=12))),
        ]
    )

    total = stack.cost()

    assert total == CostRecord(usd=7.0, tokens_in=60, tokens_out=25)


def test_block_short_circuits_later_defenses() -> None:
    later = FlaggingDefense()
    stack = DefenseStack([("blocker", BlockingToolCallDefense()), ("later", later)])

    result = stack.on_pre_tool_call(
        DefenseContext(), ToolCall(id="1", name="send_email", arguments={})
    )

    assert result.verdict is Verdict.BLOCK
    assert result.reason == "denied"
    assert later.called is False


def test_allow_verdict_reason_survives_the_stack() -> None:
    """Regression: an ALLOW-verdict reason must not be silently dropped just
    because nothing blocked (S2-05's `GuardModel` relies on this to log a
    score on every tool result, not just blocked ones)."""
    stack = DefenseStack([("guard", AllowWithReasonDefense('{"score": 0.9}'))])

    result = stack.on_tool_result(DefenseContext(), ToolResult(tool_call_id="1", content="x"))

    assert result.verdict is Verdict.ALLOW
    assert result.reason == '{"score": 0.9}'


# -- S6-01: per-member attribution --------------------------------------


def test_last_member_results_records_every_member_in_call_order() -> None:
    stack = DefenseStack(
        [
            ("first", AllowWithReasonDefense("first reason")),
            ("second", AllowWithReasonDefense("second reason")),
        ]
    )

    stack.on_tool_result(DefenseContext(), ToolResult(tool_call_id="1", content="x"))

    results = stack.last_member_results["on_tool_result"]
    assert [name for name, _ in results] == ["first", "second"]
    assert results[0][1].reason == "first reason"
    assert results[1][1].reason == "second reason"


def test_last_member_results_omits_a_member_skipped_by_an_earlier_block() -> None:
    later = FlaggingDefense()
    stack = DefenseStack([("blocker", BlockingToolCallDefense()), ("later", later)])

    stack.on_pre_tool_call(DefenseContext(), ToolCall(id="1", name="send_email", arguments={}))

    results = stack.last_member_results["on_pre_tool_call"]
    assert [name for name, _ in results] == ["blocker"]


def test_last_member_results_is_populated_fresh_each_call_not_accumulated() -> None:
    stack = DefenseStack([("a", AllowWithReasonDefense("r"))])

    stack.on_tool_result(DefenseContext(), ToolResult(tool_call_id="1", content="x"))
    stack.on_tool_result(DefenseContext(), ToolResult(tool_call_id="2", content="y"))

    assert len(stack.last_member_results["on_tool_result"]) == 1


# -- resolve_defense_stack ------------------------------------------------


def test_resolve_defense_stack_single_name_matches_resolve_defense() -> None:
    stack = resolve_defense_stack("no_defense")

    assert [name for name, _ in stack.named_defenses] == ["no_defense"]
    assert isinstance(stack.named_defenses[0][1], type(resolve_defense("no_defense")))


def test_resolve_defense_stack_composite_name_resolves_every_member() -> None:
    stack = resolve_defense_stack("spotlighting+guard_model")

    assert [name for name, _ in stack.named_defenses] == ["spotlighting", "guard_model"]


def test_resolve_defense_stack_unknown_segment_raises_naming_it() -> None:
    with pytest.raises(ValueError, match="unknown"):
        resolve_defense_stack("no_defense+unknown")
