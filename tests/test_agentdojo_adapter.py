from agentdojo.functions_runtime import FunctionCall, FunctionsRuntime, make_function

from injection_pareto.adapters.agentdojo_adapter import (
    _agentdojo_messages_to_ours,
    _build_tool_schema,
    _EpisodeRecorder,
    _make_defended_runtime_class,
    _PreGenerateElement,
    _response_to_assistant_message,
)
from injection_pareto.clients.base import ModelResponse
from injection_pareto.defenses.stack import DefenseStack
from injection_pareto.types import (
    CostRecord,
    DefenseContext,
    HookResult,
    Message,
    ToolCall,
    ToolResult,
    Verdict,
)


def greet(name: str) -> str:
    """Greets someone.

    :param name: The name to greet.
    """
    return f"hello {name}"


class _AllowDefense:
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


class _BlockDefense(_AllowDefense):
    def on_pre_tool_call(
        self, context: DefenseContext, tool_call: ToolCall
    ) -> HookResult[ToolCall]:
        return HookResult(value=tool_call, verdict=Verdict.BLOCK, reason="not allowed")


class _RedactResultDefense(_AllowDefense):
    def on_tool_result(
        self, context: DefenseContext, tool_result: ToolResult
    ) -> HookResult[ToolResult]:
        redacted = ToolResult(tool_call_id=tool_result.tool_call_id, content="[REDACTED]")
        return HookResult(value=redacted)


class _UppercaseSystemPromptDefense(_AllowDefense):
    def on_pre_generate(
        self, context: DefenseContext, messages: list[Message]
    ) -> HookResult[list[Message]]:
        mutated = [
            Message(role=m.role, content=m.content.upper()) if m.role == "system" else m
            for m in messages
        ]
        return HookResult(value=mutated)


def _context() -> DefenseContext:
    return DefenseContext(task_id="user_task_0")


def _recorder() -> _EpisodeRecorder:
    return _EpisodeRecorder()


def test_defended_runtime_allows_and_records_both_hooks() -> None:
    stack = DefenseStack([_AllowDefense()])
    recorder = _recorder()
    runtime_class = _make_defended_runtime_class(stack, _context(), recorder)
    runtime = runtime_class([make_function(greet)])

    result, error = runtime.run_function(None, "greet", {"name": "world"})

    assert result == "hello world"
    assert error is None
    assert len(recorder.tool_call_events) == 1
    hooks = [e.hook for e in recorder.tool_call_events[0]]
    assert hooks == ["on_pre_tool_call", "on_tool_result"]


def test_defended_runtime_blocks_without_calling_the_real_function() -> None:
    stack = DefenseStack([_BlockDefense()])
    recorder = _recorder()
    runtime_class = _make_defended_runtime_class(stack, _context(), recorder)
    runtime = runtime_class([make_function(greet)])

    result, error = runtime.run_function(None, "greet", {"name": "world"})

    assert result == ""
    assert error == "Blocked by defense: not allowed"
    assert len(recorder.tool_call_events) == 1
    hooks = [e.hook for e in recorder.tool_call_events[0]]
    assert hooks == ["on_pre_tool_call"]  # on_tool_result never fires for a blocked call


def test_defended_runtime_applies_tool_result_mutation() -> None:
    stack = DefenseStack([_RedactResultDefense()])
    recorder = _recorder()
    runtime_class = _make_defended_runtime_class(stack, _context(), recorder)
    runtime = runtime_class([make_function(greet)])

    result, error = runtime.run_function(None, "greet", {"name": "world"})

    assert result == "[REDACTED]"
    assert error is None


def test_agentdojo_messages_to_ours_round_trips_tool_calls() -> None:
    raw_messages = [
        {"role": "system", "content": [{"type": "text", "content": "be helpful"}]},
        {"role": "user", "content": [{"type": "text", "content": "greet the world"}]},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [FunctionCall(id="call_1", function="greet", args={"name": "world"})],
        },
        {
            "role": "tool",
            "content": [{"type": "text", "content": "hello world"}],
            "tool_call_id": "call_1",
            "error": None,
        },
    ]

    converted = _agentdojo_messages_to_ours(raw_messages)

    assert [m.role for m in converted] == ["system", "user", "assistant", "tool"]
    assert converted[0].content == "be helpful"
    expected_tool_call = ToolCall(id="call_1", name="greet", arguments={"name": "world"})
    assert converted[2].tool_calls == [expected_tool_call]
    assert converted[3].tool_call_id == "call_1"
    assert converted[3].content == "hello world"


def test_build_tool_schema_reflects_function_json_schema() -> None:
    runtime = FunctionsRuntime([make_function(greet)])

    schema = _build_tool_schema(runtime)

    assert len(schema) == 1
    assert schema[0]["type"] == "function"
    assert schema[0]["function"]["name"] == "greet"
    assert "name" in schema[0]["function"]["parameters"]["properties"]


def test_response_to_assistant_message_carries_tool_calls() -> None:
    response = ModelResponse(
        text="",
        tool_calls=[ToolCall(id="call_1", name="greet", arguments={"name": "world"})],
        tokens_in=10,
        tokens_out=5,
        wall_ms=10,
        cost=CostRecord(),
        raw={},
    )

    message = _response_to_assistant_message(response)

    assert message["role"] == "assistant"
    assert message["content"] is None
    assert message["tool_calls"][0].function == "greet"
    assert message["tool_calls"][0].args == {"name": "world"}


def test_pre_generate_element_no_mutation_is_a_no_op() -> None:
    messages = [{"role": "system", "content": [{"type": "text", "content": "be helpful"}]}]
    stack = DefenseStack([_AllowDefense()])
    element = _PreGenerateElement(stack, _context(), _recorder())

    _, _, _, result_messages, _ = element.query("hi", FunctionsRuntime([]), messages=messages)

    assert result_messages is messages


def test_pre_generate_element_applies_content_mutation() -> None:
    messages = [{"role": "system", "content": [{"type": "text", "content": "be helpful"}]}]
    stack = DefenseStack([_UppercaseSystemPromptDefense()])
    element = _PreGenerateElement(stack, _context(), _recorder())

    _, _, _, result_messages, _ = element.query("hi", FunctionsRuntime([]), messages=messages)

    assert result_messages[0]["content"][0]["content"] == "BE HELPFUL"
