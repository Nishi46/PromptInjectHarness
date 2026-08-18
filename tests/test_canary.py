from injection_pareto.defenses import Canary, resolve_defense
from injection_pareto.types import DefenseContext, Message, ToolCall, ToolResult, Verdict


def test_first_call_inserts_probe_into_system_message() -> None:
    defense = Canary(token="ABC123")
    messages = [Message(role="system", content="be helpful"), Message(role="user", content="hi")]

    result = defense.on_pre_generate(DefenseContext(), messages)

    assert result.value[0].content.startswith("be helpful")
    assert "ABC123" in result.value[0].content
    assert result.verdict is Verdict.ALLOW  # no assistant turn to check yet


def test_probe_insertion_is_idempotent_across_turns() -> None:
    defense = Canary(token="ABC123")
    messages = [Message(role="system", content="be helpful"), Message(role="user", content="hi")]

    first = defense.on_pre_generate(DefenseContext(), messages)
    # Simulate the next turn: same system message (already mutated), plus an
    # assistant reply that correctly echoed the token.
    second_input = [
        *first.value,
        Message(role="assistant", content="Here you go.\nABC123"),
    ]
    second = defense.on_pre_generate(DefenseContext(), second_input)

    assert second.value is second_input  # no further mutation
    assert second.value[0].content.count("Instruction-following check") == 1


def test_matching_token_in_last_response_allows() -> None:
    defense = Canary(token="ABC123")
    messages = [Message(role="system", content="be helpful"), Message(role="user", content="hi")]
    probed = defense.on_pre_generate(DefenseContext(), messages).value

    turn = [*probed, Message(role="assistant", content="The answer is 42.\nABC123")]
    result = defense.on_pre_generate(DefenseContext(), turn)

    assert result.verdict is Verdict.ALLOW


def test_missing_token_in_last_response_blocks() -> None:
    defense = Canary(token="ABC123")
    messages = [Message(role="system", content="be helpful"), Message(role="user", content="hi")]
    probed = defense.on_pre_generate(DefenseContext(), messages).value

    turn = [*probed, Message(role="assistant", content="The answer is 42.")]
    result = defense.on_pre_generate(DefenseContext(), turn)

    assert result.verdict is Verdict.BLOCK
    assert result.reason is not None
    assert "ABC123" in result.reason


def test_tool_call_only_turn_with_no_text_is_not_flagged() -> None:
    defense = Canary(token="ABC123")
    messages = [Message(role="system", content="be helpful"), Message(role="user", content="hi")]
    probed = defense.on_pre_generate(DefenseContext(), messages).value

    tool_call = ToolCall(id="1", name="get_weather", arguments={})
    turn = [*probed, Message(role="assistant", content="", tool_calls=[tool_call])]
    result = defense.on_pre_generate(DefenseContext(), turn)

    assert result.verdict is Verdict.ALLOW
    assert result.reason is None


def test_on_pre_tool_call_and_on_tool_result_are_pass_throughs() -> None:
    defense = Canary(token="ABC123")
    context = DefenseContext()
    tool_call = ToolCall(id="1", name="send_email", arguments={})
    tool_result = ToolResult(tool_call_id="1", content="sent")

    assert defense.on_pre_tool_call(context, tool_call).value is tool_call
    assert defense.on_tool_result(context, tool_result).value is tool_result


def test_cost_is_zero() -> None:
    assert Canary().cost().usd == 0.0


def test_default_token_is_random_per_instance() -> None:
    messages = [Message(role="system", content="be helpful"), Message(role="user", content="hi")]

    probe_a = Canary().on_pre_generate(DefenseContext(), messages).value[0].content
    probe_b = Canary().on_pre_generate(DefenseContext(), messages).value[0].content

    assert probe_a != probe_b
    assert "Instruction-following check" in probe_a


def test_resolve_defense_returns_canary_instance() -> None:
    assert isinstance(resolve_defense("canary"), Canary)
