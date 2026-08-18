from injection_pareto.defenses import ToolAllowlist, resolve_defense
from injection_pareto.types import DefenseContext, Message, ToolCall, ToolResult, Verdict


def _context(task_id: str = "user_task_0", user_task_prompt: str | None = "hi") -> DefenseContext:
    metadata = {"user_task_prompt": user_task_prompt} if user_task_prompt is not None else {}
    return DefenseContext(task_id=task_id, metadata=metadata)


def _reason(reason: str | None) -> str:
    assert reason is not None
    return reason


def test_allowed_tool_and_argument_pass() -> None:
    defense = ToolAllowlist(task_allowlists={"user_task_0": frozenset({"send_email"})})
    context = _context(user_task_prompt="please email alice@example.com the update")
    tool_call = ToolCall(id="1", name="send_email", arguments={"recipients": ["alice@example.com"]})

    result = defense.on_pre_tool_call(context, tool_call)

    assert result.verdict is Verdict.ALLOW
    assert result.value is tool_call


def test_tool_not_in_allowlist_is_blocked() -> None:
    defense = ToolAllowlist(task_allowlists={"user_task_0": frozenset({"get_calendar_events"})})
    context = _context()
    tool_call = ToolCall(id="1", name="send_email", arguments={})

    result = defense.on_pre_tool_call(context, tool_call)

    assert result.verdict is Verdict.BLOCK
    assert "not in the allowlist" in _reason(result.reason)


def test_no_allowlist_entry_for_task_means_no_restriction() -> None:
    defense = ToolAllowlist(task_allowlists={"some_other_task": frozenset({"get_calendar_events"})})
    context = _context(task_id="user_task_0")
    tool_call = ToolCall(id="1", name="send_email", arguments={})

    result = defense.on_pre_tool_call(context, tool_call)

    assert result.verdict is Verdict.ALLOW


def test_recipient_not_in_original_request_is_blocked() -> None:
    defense = ToolAllowlist()
    context = _context(user_task_prompt="Who else is invited to the networking event?")
    tool_call = ToolCall(
        id="1", name="send_email", arguments={"recipients": ["mark.black-2134@gmail.com"]}
    )

    result = defense.on_pre_tool_call(context, tool_call)

    assert result.verdict is Verdict.BLOCK
    reason = _reason(result.reason)
    assert "mark.black-2134@gmail.com" in reason
    assert "does not appear" in reason


def test_recipient_in_original_request_is_allowed() -> None:
    defense = ToolAllowlist()
    context = _context(user_task_prompt="Please email alice@example.com about the meeting")
    tool_call = ToolCall(id="1", name="send_email", arguments={"recipients": ["alice@example.com"]})

    result = defense.on_pre_tool_call(context, tool_call)

    assert result.verdict is Verdict.ALLOW


def test_single_string_recipient_argument_is_also_checked() -> None:
    """`recipients` is a list in AgentDojo's workspace suite, but the check
    must not assume every constrained argument is list-shaped."""
    defense = ToolAllowlist()
    context = _context(user_task_prompt="hi")
    tool_call = ToolCall(id="1", name="send_email", arguments={"to": "attacker@evil.com"})

    result = defense.on_pre_tool_call(context, tool_call)

    assert result.verdict is Verdict.BLOCK


def test_unconstrained_argument_names_are_ignored() -> None:
    defense = ToolAllowlist()
    context = _context(user_task_prompt="hi")
    tool_call = ToolCall(id="1", name="send_email", arguments={"subject": "not a recipient field"})

    result = defense.on_pre_tool_call(context, tool_call)

    assert result.verdict is Verdict.ALLOW


def test_missing_user_task_prompt_skips_argument_check_without_blocking() -> None:
    defense = ToolAllowlist()
    context = _context(user_task_prompt=None)
    tool_call = ToolCall(
        id="1", name="send_email", arguments={"recipients": ["anyone@example.com"]}
    )

    result = defense.on_pre_tool_call(context, tool_call)

    assert result.verdict is Verdict.ALLOW


def test_on_pre_generate_and_on_tool_result_are_pass_throughs() -> None:
    defense = ToolAllowlist()
    context = _context()
    messages = [Message(role="user", content="hi")]
    tool_result = ToolResult(tool_call_id="1", content="ok")

    assert defense.on_pre_generate(context, messages).value is messages
    assert defense.on_tool_result(context, tool_result).value is tool_result


def test_cost_is_zero() -> None:
    assert ToolAllowlist().cost().usd == 0.0


def test_resolve_defense_returns_tool_allowlist_instance() -> None:
    assert isinstance(resolve_defense("tool_allowlist"), ToolAllowlist)
