from injection_pareto.defenses import InstructionalPrevention, resolve_defense
from injection_pareto.types import DefenseContext, Message, ToolCall, ToolResult


def test_on_pre_generate_appends_hardening_to_system_message_only() -> None:
    defense = InstructionalPrevention()
    messages = [
        Message(role="system", content="be helpful"),
        Message(role="user", content="hi"),
    ]

    result = defense.on_pre_generate(DefenseContext(), messages)

    assert result.value[0].content.startswith("be helpful")
    assert "untrusted data" in result.value[0].content
    assert result.value[1] is messages[1]  # non-system messages untouched


def test_on_pre_generate_is_idempotent_across_turns() -> None:
    """Must not duplicate the addendum when this hook fires again on a later
    turn (matches the adapter's `_PreGenerateElement`, which fires before
    every LLM call, not just the first)."""
    defense = InstructionalPrevention()
    messages = [Message(role="system", content="be helpful"), Message(role="user", content="hi")]

    first = defense.on_pre_generate(DefenseContext(), messages)
    second = defense.on_pre_generate(DefenseContext(), first.value)

    assert second.value is first.value
    assert first.value[0].content.count("untrusted data") == 1


def test_on_pre_tool_call_and_on_tool_result_are_pure_pass_throughs() -> None:
    defense = InstructionalPrevention()
    context = DefenseContext()
    tool_call = ToolCall(id="1", name="send_email", arguments={})
    tool_result = ToolResult(tool_call_id="1", content="sent")

    assert defense.on_pre_tool_call(context, tool_call).value is tool_call
    assert defense.on_tool_result(context, tool_result).value is tool_result


def test_cost_is_zero() -> None:
    assert InstructionalPrevention().cost().usd == 0.0


def test_resolve_defense_returns_instructional_prevention_instance() -> None:
    assert isinstance(resolve_defense("instructional_prevention"), InstructionalPrevention)
