import json
from pathlib import Path
from typing import Any

import pytest
from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.attacks.attack_registry import load_attack
from agentdojo.functions_runtime import FunctionCall, FunctionsRuntime, make_function
from agentdojo.task_suite.load_suites import get_suite

from injection_pareto.adapters.agentdojo_adapter import (
    BENCHMARK_VERSION,
    _agentdojo_messages_to_ours,
    _build_tool_schema,
    _DefenseEventRecord,
    _EpisodeRecorder,
    _make_defended_runtime_class,
    _PreGenerateElement,
    _response_to_assistant_message,
    _write_episode_trace,
    run_episode,
)
from injection_pareto.attacks import resolve_attack_name
from injection_pareto.clients.base import ModelRequest, ModelResponse
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

# Same known-good combo as S1-08's reproduction / test_attack_families.py: a
# real suite/task/injection task, no live model call needed.
_SUITE_NAME = "workspace"
_USER_TASK_ID = "user_task_0"
_INJECTION_TASK_ID = "injection_task_0"


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
    stack = DefenseStack([("allow", _AllowDefense())])
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
    stack = DefenseStack([("block", _BlockDefense())])
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
    stack = DefenseStack([("redact", _RedactResultDefense())])
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
    stack = DefenseStack([("allow", _AllowDefense())])
    element = _PreGenerateElement(stack, _context(), _recorder())

    _, _, _, result_messages, _ = element.query("hi", FunctionsRuntime([]), messages=messages)

    assert result_messages is messages


def test_pre_generate_element_applies_content_mutation() -> None:
    messages = [{"role": "system", "content": [{"type": "text", "content": "be helpful"}]}]
    stack = DefenseStack([("uppercase", _UppercaseSystemPromptDefense())])
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
        defense_name="allow",
        hook="on_pre_tool_call",
        verdict="allow",
        reason=None,
        timestamp="stale",
    )
    recorder.tool_call_events.append([stale_event])
    recorder.pre_generate_events.append(
        _DefenseEventRecord(
            defense_name="allow",
            hook="on_pre_generate",
            verdict="allow",
            reason=None,
            timestamp="stale",
        )
    )
    element = _PreGenerateElement(DefenseStack([("allow", _AllowDefense())]), _context(), recorder)

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


class _FakeGuardResponse:
    def __init__(self, usd: float, tokens_in: int, tokens_out: int, wall_ms: int) -> None:
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.wall_ms = wall_ms
        self.cost = CostRecord(usd=usd, tokens_in=tokens_in, tokens_out=tokens_out)
        self.cache_hit = False


class _FakeGuardDefense(_AllowDefense):
    """Stands in for S2-05's `GuardModel`: exposes a `responses` attribute
    that `_write_episode_trace` duck-types on to record defense-internal
    model calls separately from the agent's own."""

    def __init__(self, responses: list[_FakeGuardResponse]) -> None:
        self.responses = responses


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


def test_write_episode_trace_records_defense_model_calls_separately_from_agent(
    tmp_path: Path,
) -> None:
    """S2-05: a defense that makes its own model calls (e.g. `GuardModel`)
    exposes them via `.responses`; those must land in `cost_record` labeled
    `defense:<defense_name>`, distinct from the agent's own `model_name`-
    labeled rows, so overhead is separable in the DB (Sprint 2 acceptance
    criterion)."""
    conn, run_id, episode_id = _open_test_db(tmp_path)
    try:
        messages = [
            {"role": "system", "content": [{"type": "text", "content": "sys"}]},
            {"role": "user", "content": [{"type": "text", "content": "go"}]},
        ]
        recorder = _recorder()
        recorder.model_responses.append(
            ModelResponse(
                text="hi", tool_calls=[], tokens_in=100, tokens_out=50, wall_ms=200,
                cost=CostRecord(usd=0.01, tokens_in=100, tokens_out=50), raw={},
            )
        )
        guard_defense = _FakeGuardDefense(
            [_FakeGuardResponse(usd=0.0005, tokens_in=10, tokens_out=2, wall_ms=5)]
        )
        stack = DefenseStack([("guard_model", guard_defense)])

        _write_episode_trace(
            conn,
            run_id=run_id,
            episode_id=episode_id,
            messages=messages,
            recorder=recorder,
            model_name="llama3.2:3b",
            valid_function_names=set(),
            defense_stack=stack,
        )

        rows = {r["model"]: r for r in conn.execute("SELECT model, usd, wall_ms FROM cost_record")}
    finally:
        conn.close()

    assert rows["llama3.2:3b"]["usd"] == 0.01
    assert rows["defense:guard_model"]["usd"] == 0.0005
    assert rows["defense:guard_model"]["wall_ms"] == 5


def test_composed_stack_attributes_defense_events_and_cost_per_member(tmp_path: Path) -> None:
    """S6-01: a two-member stack must produce one `defense_event` row per
    member per hook (not one aggregate row for the whole stack), and one
    separately-labeled `cost_record` row per member that made its own
    model calls -- the "cost attributed per layer" acceptance criterion,
    exercised through the real recording path (`_make_defended_runtime_class`),
    not just the cost-record loop in isolation."""
    guard_a = _FakeGuardDefense(
        [_FakeGuardResponse(usd=0.001, tokens_in=10, tokens_out=2, wall_ms=5)]
    )
    guard_b = _FakeGuardDefense(
        [_FakeGuardResponse(usd=0.002, tokens_in=20, tokens_out=4, wall_ms=7)]
    )
    stack = DefenseStack([("guard_a", guard_a), ("guard_b", guard_b)])
    recorder = _recorder()
    runtime_class = _make_defended_runtime_class(stack, _context(), recorder)
    runtime = runtime_class([make_function(greet)])

    runtime.run_function(None, "greet", {"name": "world"})

    assert len(recorder.tool_call_events) == 1
    events = recorder.tool_call_events[0]
    # 2 members x 2 hooks (on_pre_tool_call, on_tool_result) = 4 events,
    # each carrying its own member's name.
    assert [(e.defense_name, e.hook) for e in events] == [
        ("guard_a", "on_pre_tool_call"),
        ("guard_b", "on_pre_tool_call"),
        ("guard_a", "on_tool_result"),
        ("guard_b", "on_tool_result"),
    ]

    conn, run_id, episode_id = _open_test_db(tmp_path)
    try:
        _write_episode_trace(
            conn,
            run_id=run_id,
            episode_id=episode_id,
            messages=[
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [FunctionCall(id="1", function="greet", args={"name": "world"})],
                },
                {
                    "role": "tool",
                    "content": [{"type": "text", "content": "hello world"}],
                    "tool_call_id": "1",
                    "error": None,
                },
            ],
            recorder=recorder,
            model_name="llama3.2:3b",
            valid_function_names={"greet"},
            defense_stack=stack,
        )

        defense_event_names = [
            r["defense_name"]
            for r in conn.execute(
                "SELECT defense_name FROM defense_event ORDER BY id"
            ).fetchall()
        ]
        cost_rows = {
            r["model"]: r for r in conn.execute("SELECT model, usd, wall_ms FROM cost_record")
        }
    finally:
        conn.close()

    assert defense_event_names == ["guard_a", "guard_b", "guard_a", "guard_b"]
    assert cost_rows["defense:guard_a"]["usd"] == 0.001
    assert cost_rows["defense:guard_a"]["wall_ms"] == 5
    assert cost_rows["defense:guard_b"]["usd"] == 0.002
    assert cost_rows["defense:guard_b"]["wall_ms"] == 7


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
                    defense_name="no_defense",
                    hook="on_pre_tool_call",
                    verdict="allow",
                    reason=None,
                    timestamp="t0",
                ),
                _DefenseEventRecord(
                    defense_name="no_defense",
                    hook="on_tool_result",
                    verdict="block",
                    reason="denied",
                    timestamp="t0",
                ),
            ]
        )

        step_count, tool_call_count = _write_episode_trace(
            conn,
            run_id=run_id,
            episode_id=episode_id,
            messages=messages,
            recorder=recorder,
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


class _UnusedModelClient:
    """A `ModelClient` that must never be called -- used in the
    `injections_override` tests below, which monkeypatch
    `suite.run_task_with_pipeline` itself, so the pipeline (and therefore
    this client) never actually runs."""

    cache_model_id = "fake:unused"

    def generate(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError("model_client.generate should not be called")


def _run_episode_capturing_injections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    injections_override: dict[str, str] | None,
    patch_load_attack: bool,
) -> dict[str, Any]:
    """Shared plumbing for the two `injections_override` tests below:
    monkeypatches the real `workspace` suite's `run_task_with_pipeline` to
    capture the `injections` dict it was called with instead of actually
    running the pipeline (no model call needed), runs `run_episode` for
    real against it, and returns the captured call's kwargs."""
    suite = get_suite(BENCHMARK_VERSION, _SUITE_NAME)
    captured: dict[str, Any] = {}

    def _fake_run_task_with_pipeline(
        pipeline: object,
        user_task: object,
        injection_task: object,
        injections: dict[str, str],
        *,
        runtime_class: object,
    ) -> tuple[bool, bool]:
        captured["injections"] = injections
        return True, True

    monkeypatch.setattr(suite, "run_task_with_pipeline", _fake_run_task_with_pipeline)

    if patch_load_attack:

        def _boom(*args: object, **kwargs: object) -> object:
            raise AssertionError("load_attack should not be called when injections_override is set")

        monkeypatch.setattr("injection_pareto.adapters.agentdojo_adapter.load_attack", _boom)

    conn = connect(tmp_path / "trace.db")
    init_db(conn)
    try:
        run_id = insert_run(
            conn,
            config_hash="x",
            model="llama3.2:3b",
            defense_stack="no_defense",
            suite=_SUITE_NAME,
            started_at="t0",
        )
        result = run_episode(
            conn=conn,
            run_id=run_id,
            suite_name=_SUITE_NAME,
            user_task_id=_USER_TASK_ID,
            injection_task_id=_INJECTION_TASK_ID,
            model_client=_UnusedModelClient(),
            defense_stack=DefenseStack([("no_defense", _AllowDefense())]),
            defense_name="no_defense",
            model_name="llama3.2:3b",
            attack_name="naive",
            injections_override=injections_override,
        )
    finally:
        conn.close()

    captured["result"] = result
    return captured


def test_injections_override_bypasses_load_attack_and_reaches_the_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = {"injection_point": "an entirely mutated payload"}

    captured = _run_episode_capturing_injections(
        tmp_path, monkeypatch, injections_override=override, patch_load_attack=True
    )

    assert captured["injections"] == override
    result = captured["result"]
    assert result.utility is True
    assert result.security is True


def test_omitting_injections_override_reproduces_todays_load_attack_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _run_episode_capturing_injections(
        tmp_path, monkeypatch, injections_override=None, patch_load_attack=False
    )

    class _FakePipeline(BasePipelineElement):
        name = "local-llama3.2:3b-no_defense"

        def query(self, *args: object, **kwargs: object) -> object:
            raise NotImplementedError

    suite = get_suite(BENCHMARK_VERSION, _SUITE_NAME)
    user_task = suite.get_user_task_by_id(_USER_TASK_ID)
    injection_task = suite.get_injection_task_by_id(_INJECTION_TASK_ID)
    attack = load_attack(resolve_attack_name("naive"), suite, _FakePipeline())
    expected_injections = attack.attack(user_task, injection_task)

    assert captured["injections"] == expected_injections
