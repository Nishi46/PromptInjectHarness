import pytest

from injection_pareto.defenses import NoDefense, resolve_defense
from injection_pareto.types import DefenseContext, Message, ToolCall, ToolResult, Verdict


def test_no_defense_is_a_pure_pass_through() -> None:
    defense = NoDefense()
    context = DefenseContext()
    messages = [Message(role="user", content="hi")]
    tool_call = ToolCall(id="1", name="send_email", arguments={})
    tool_result = ToolResult(tool_call_id="1", content="sent")

    pre_generate = defense.on_pre_generate(context, messages)
    pre_tool_call = defense.on_pre_tool_call(context, tool_call)
    post_tool_result = defense.on_tool_result(context, tool_result)

    assert pre_generate.value is messages
    assert pre_generate.verdict is Verdict.ALLOW
    assert pre_tool_call.value is tool_call
    assert post_tool_result.value is tool_result
    assert defense.cost().usd == 0.0


def test_resolve_defense_returns_no_defense_instance() -> None:
    assert isinstance(resolve_defense("no_defense"), NoDefense)


def test_resolve_defense_raises_clear_error_for_unknown_name() -> None:
    with pytest.raises(ValueError, match="Unknown defense"):
        resolve_defense("not_a_real_defense")
