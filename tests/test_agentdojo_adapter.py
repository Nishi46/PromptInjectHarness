import json
from pathlib import Path

from agentdojo.functions_runtime import FunctionCall, FunctionsRuntime, make_function

from injection_pareto.adapters.agentdojo_adapter import (
    _agentdojo_messages_to_ours,
    _build_tool_schema,
    _DefenseEventRecord,
    _EpisodeRecorder,
    _make_defended_runtime_class,
    _PreGenerateElement,
    _response_to_assistant_message,
    _write_episode_trace,
)
from injection_pareto.clients.base import ModelResponse
from injection_pareto.defenses.stack import DefenseStack
from injection_pareto.trace import connect, init_db, insert_episode, insert_run
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


def test_pre_generate_element_resets_recorder_on_fresh_attempt() -> None:
    """Regression: AgentDojo's internal retry loop (`run_task_with_pipeline`,
    up to 3x on a malformed final output) calls the pipeline fresh each time
    — [system, user] is the state only ever seen at the top of an attempt, so
    it must clear out whatever a discarded earlier attempt left behind."""
    recorder = _recorder()
    stale_event = _DefenseEventRecord(
        hook="on_pre_tool_call", verdict="allow", reason=None, timestamp="stale"
    )
    recorder.tool_call_events.append([stale_event])
    recorder.pre_generate_events.append(
        _DefenseEventRecord(hook="on_pre_generate", verdict="allow", reason=None, timestamp="stale")
    )
    element = _PreGenerateElement(DefenseStack([_AllowDefense()]), _context(), recorder)

    messages = [
        {"role": "system", "content": [{"type": "text", "content": "sys"}]},
        {"role": "user", "content": [{"type": "text", "content": "go"}]},
    ]
    element.query("go", FunctionsRuntime([]), messages=messages)

    # tool_call_events has no analog produced by this call, so the stale
    # entry is simply gone. pre_generate_events gets exactly this call's own
    # new entry — the stale one, not that.
    assert recorder.tool_call_events == []
    assert len(recorder.pre_generate_events) == 1
    assert recorder.pre_generate_events[0].timestamp != "stale"


def test_recorder_reset_keeps_model_responses() -> None:
    """model_responses is deliberately not cleared by a reset: those API
    calls really happened and really cost tokens/$, independent of which
    attempt's messages end up in the final trace."""
    recorder = _recorder()
    response = ModelResponse(
        text="hi", tool_calls=[], tokens_in=1, tokens_out=1, wall_ms=1, cost=CostRecord(), raw={}
    )
    recorder.model_responses.append(response)

    recorder.reset_for_new_attempt()

    assert recorder.model_responses == [response]


def _open_test_db(tmp_path: Path) -> tuple:
    conn = connect(tmp_path / "trace.db")
    init_db(conn)
    run_id = insert_run(
        conn,
        config_hash="x",
        model="llama3.2:3b",
        defense_stack="no_defense",
        suite="workspace",
        started_at="t0",
    )
    episode_id = insert_episode(conn, run_id=run_id, task_id="user_task_0", started_at="t0")
    return conn, run_id, episode_id


def test_write_episode_trace_handles_max_iters_cutoff_without_crashing(tmp_path: Path) -> None:
    """Regression: `ToolsExecutionLoop` can hit `max_iters` right after the
    LLM requests a tool call but before `ToolsExecutor` ever runs it — the
    transcript ends with an assistant message whose `tool_calls` have no
    result message at all. Must not crash, and must still record the
    requested-but-never-executed call."""
    conn, run_id, episode_id = _open_test_db(tmp_path)
    try:
        messages = [
            {"role": "system", "content": [{"type": "text", "content": "sys"}]},
            {"role": "user", "content": [{"type": "text", "content": "go"}]},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [FunctionCall(id="1", function="send_email", args={})],
            },
        ]
        recorder = _recorder()  # no tool_call_events — run_function never ran

        step_count, tool_call_count = _write_episode_trace(
            conn,
            run_id=run_id,
            episode_id=episode_id,
            messages=messages,
            recorder=recorder,
            defense_name="no_defense",
            model_name="llama3.2:3b",
            valid_function_names={"send_email"},
        )

        row = conn.execute("SELECT result_json, blocked_by_defense FROM tool_call").fetchone()
    finally:
        conn.close()

    assert step_count == 3
    assert tool_call_count == 1
    assert row["result_json"] is None
    assert row["blocked_by_defense"] is None


def test_write_episode_trace_does_not_misalign_cursor_on_invalid_tool_name(tmp_path: Path) -> None:
    """Regression: `ToolsExecutor` rejects an unregistered function name
    before ever calling `FunctionsRuntime.run_function` — it still appends
    an error result message, but `recorder.tool_call_events` gets no entry
    for that call. A later *valid* call in the same batch must still get
    its own (correct) recorder entry, not the invalid call's missing one."""
    conn, run_id, episode_id = _open_test_db(tmp_path)
    try:
        messages = [
            {"role": "system", "content": [{"type": "text", "content": "sys"}]},
            {"role": "user", "content": [{"type": "text", "content": "go"}]},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    FunctionCall(id="1", function="not_a_real_tool", args={}),
                    FunctionCall(id="2", function="send_email", args={"to": "a@b.com"}),
                ],
            },
            {
                "role": "tool",
                "content": [{"type": "text", "content": ""}],
                "tool_call_id": "1",
                "error": "ToolNotFoundError: not_a_real_tool",
            },
            {
                "role": "tool",
                "content": [{"type": "text", "content": "sent"}],
                "tool_call_id": "2",
                "error": None,
            },
        ]
        # Only ONE recorder entry: the invalid call above never reached our
        # defended runtime, so it never appended anything.
        recorder = _recorder()
        recorder.tool_call_events.append(
            [
                _DefenseEventRecord(
                    hook="on_pre_tool_call", verdict="allow", reason=None, timestamp="t0"
                ),
                _DefenseEventRecord(
                    hook="on_tool_result", verdict="block", reason="denied", timestamp="t0"
                ),
            ]
        )

        step_count, tool_call_count = _write_episode_trace(
            conn,
            run_id=run_id,
            episode_id=episode_id,
            messages=messages,
            recorder=recorder,
            defense_name="no_defense",
            model_name="llama3.2:3b",
            valid_function_names={"send_email"},
        )

        tool_call_rows = conn.execute("SELECT id, tool_name FROM tool_call ORDER BY id").fetchall()
        defense_events = conn.execute(
            "SELECT hook, verdict, detail_json FROM defense_event"
        ).fetchall()
    finally:
        conn.close()

    assert step_count == 3
    assert tool_call_count == 2
    assert [r["tool_name"] for r in tool_call_rows] == ["not_a_real_tool", "send_email"]

    # Exactly the one recorder entry's 2 events — not duplicated or dropped,
    # and not misattributed to the invalid call that precedes it.
    assert len(defense_events) == 2
    send_email_id = next(r["id"] for r in tool_call_rows if r["tool_name"] == "send_email")
    result_event = next(e for e in defense_events if e["hook"] == "on_tool_result")
    assert result_event["verdict"] == "block"
    assert json.loads(result_event["detail_json"])["tool_call_id"] == send_email_id
