from injection_pareto.defenses import CapabilityEnforcement, resolve_defense
from injection_pareto.defenses.capability_enforcement import _extract_candidate_values
from injection_pareto.types import DefenseContext, Message, ToolCall, ToolResult, Verdict


def _context(*, suite: str = "workspace", user_task_prompt: str = "") -> DefenseContext:
    return DefenseContext(metadata={"suite": suite, "user_task_prompt": user_task_prompt})


def _tool_result(content: str) -> ToolResult:
    return ToolResult(tool_call_id="1", content=content)


def _tool_call(name: str, arguments: dict) -> ToolCall:
    return ToolCall(id="1", name=name, arguments=arguments)


# -- _extract_candidate_values ------------------------------------------------


def test_extract_candidate_values_finds_an_email_address() -> None:
    values = _extract_candidate_values("contact jane.doe@example.com for details")
    assert "jane.doe@example.com" in values


def test_extract_candidate_values_finds_a_url() -> None:
    values = _extract_candidate_values("see https://evil.example/exfil for more")
    assert "https://evil.example/exfil" in values


def test_extract_candidate_values_finds_quoted_substrings() -> None:
    values = _extract_candidate_values('the account number is "AC-99182" today')
    assert "AC-99182" in values


def test_extract_candidate_values_finds_single_quoted_substrings() -> None:
    values = _extract_candidate_values("the code is 'X7-42Z' for entry")
    assert "X7-42Z" in values


def test_extract_candidate_values_no_match_on_plain_text() -> None:
    assert _extract_candidate_values("the weather is nice today") == set()


# -- on_pre_generate is a pass-through ----------------------------------------


def test_on_pre_generate_is_a_pass_through() -> None:
    defense = CapabilityEnforcement()
    messages = [Message(role="user", content="hi")]

    assert defense.on_pre_generate(_context(), messages).value is messages


# -- on_tool_result taints values, never mutates ------------------------------


def test_on_tool_result_never_mutates_and_always_allows() -> None:
    defense = CapabilityEnforcement()
    original = _tool_result("attacker@evil.example sent this")

    result = defense.on_tool_result(_context(), original)

    assert result.value is original
    assert result.verdict is Verdict.ALLOW


# -- on_pre_tool_call: the policy engine --------------------------------------


def test_tainted_value_into_a_sink_tool_argument_is_blocked_by_data_flow_policy() -> None:
    defense = CapabilityEnforcement(sink_tools={"workspace": frozenset({"send_email"})})
    defense.on_tool_result(_context(), _tool_result("account number is 'AC-99182'"))

    result = defense.on_pre_tool_call(
        _context(user_task_prompt="please email the team"),
        _tool_call("send_email", {"body": "here it is: AC-99182"}),
    )

    assert result.verdict is Verdict.BLOCK
    assert result.reason is not None
    assert "blocked by data-flow policy, not content classification" in result.reason
    assert "AC-99182" in result.reason
    assert "send_email" in result.reason


def test_same_tainted_value_into_a_non_sink_tool_is_allowed() -> None:
    defense = CapabilityEnforcement(sink_tools={"workspace": frozenset({"send_email"})})
    defense.on_tool_result(_context(), _tool_result("account number is 'AC-99182'"))

    result = defense.on_pre_tool_call(
        _context(),
        _tool_call("log_note", {"body": "here it is: AC-99182"}),
    )

    assert result.verdict is Verdict.ALLOW


def test_tainted_value_also_present_in_user_task_prompt_is_allowed() -> None:
    """Trusted-origin escape hatch: a value that's tainted but also appears
    verbatim in the user's own original request isn't a leak."""
    defense = CapabilityEnforcement(sink_tools={"workspace": frozenset({"send_email"})})
    defense.on_tool_result(_context(), _tool_result("account number is 'AC-99182'"))

    result = defense.on_pre_tool_call(
        _context(user_task_prompt="please send AC-99182 to the team"),
        _tool_call("send_email", {"body": "here it is: AC-99182"}),
    )

    assert result.verdict is Verdict.ALLOW


def test_value_in_user_task_prompt_but_never_tainted_is_allowed() -> None:
    """Pure negative case: a value present in the user's own prompt, never
    seen in any tool result, must never be confused with a tainted value."""
    defense = CapabilityEnforcement(sink_tools={"workspace": frozenset({"send_email"})})

    result = defense.on_pre_tool_call(
        _context(user_task_prompt="please send AC-99182 to the team"),
        _tool_call("send_email", {"body": "here it is: AC-99182"}),
    )

    assert result.verdict is Verdict.ALLOW


def test_untainted_value_into_a_sink_tool_is_allowed() -> None:
    defense = CapabilityEnforcement(sink_tools={"workspace": frozenset({"send_email"})})

    result = defense.on_pre_tool_call(
        _context(),
        _tool_call("send_email", {"body": "just a normal message"}),
    )

    assert result.verdict is Verdict.ALLOW


def test_sink_policy_with_no_entry_for_current_suite_never_blocks() -> None:
    defense = CapabilityEnforcement(sink_tools={"mcp": frozenset({"send_email"})})
    defense.on_tool_result(_context(), _tool_result("account number is 'AC-99182'"))

    result = defense.on_pre_tool_call(
        _context(suite="workspace"),
        _tool_call("send_email", {"body": "here it is: AC-99182"}),
    )

    assert result.verdict is Verdict.ALLOW


def test_list_argument_elements_are_scanned_like_tool_allowlist() -> None:
    defense = CapabilityEnforcement(sink_tools={"workspace": frozenset({"send_email"})})
    defense.on_tool_result(_context(), _tool_result("cc 'shadow@attacker.example' quietly"))

    result = defense.on_pre_tool_call(
        _context(user_task_prompt="email the team"),
        _tool_call("send_email", {"cc": ["team@company.example", "shadow@attacker.example"]}),
    )

    assert result.verdict is Verdict.BLOCK
    assert result.reason is not None
    assert "shadow@attacker.example" in result.reason


def test_taint_state_does_not_leak_across_instances() -> None:
    defense_a = CapabilityEnforcement(sink_tools={"workspace": frozenset({"send_email"})})
    defense_b = CapabilityEnforcement(sink_tools={"workspace": frozenset({"send_email"})})

    defense_a.on_tool_result(_context(), _tool_result("account number is 'AC-99182'"))

    result = defense_b.on_pre_tool_call(
        _context(),
        _tool_call("send_email", {"body": "here it is: AC-99182"}),
    )

    assert result.verdict is Verdict.ALLOW


def test_cost_is_always_empty() -> None:
    defense = CapabilityEnforcement()
    defense.on_tool_result(_context(), _tool_result("nothing free about this call"))

    cost = defense.cost()

    assert cost.usd == 0.0
    assert cost.tokens_in == 0
    assert cost.tokens_out == 0


def test_resolve_defense_returns_capability_enforcement_instance() -> None:
    assert isinstance(resolve_defense("capability_enforcement"), CapabilityEnforcement)
