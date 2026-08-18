import uuid
from pathlib import Path

import pytest

from injection_pareto.mcp.loader import load_server
from injection_pareto.mcp.runtime import MockMCPRuntime
from injection_pareto.mcp.tasks import BENIGN_TASKS, ExpectedCall, MCPUserTask, check_completion
from injection_pareto.types import ToolCall

SERVERS_DIR = Path(__file__).parent.parent / "src" / "injection_pareto" / "mcp" / "servers"


def _call(name: str, arguments: dict | None = None) -> ToolCall:
    return ToolCall(id=str(uuid.uuid4()), name=name, arguments=arguments or {})


def _trace_for(task: MCPUserTask) -> list[ToolCall]:
    """Builds the "correct" trace directly from a task's `expected_calls` --
    valid because every one of the 15 real tasks' `expected_calls` arguments
    are literal, realistic call arguments (see the comment above
    `BENIGN_TASKS`), not just predicates."""
    return [_call(ec.tool_name, dict(ec.arguments)) for ec in task.expected_calls]


# ---------------------------------------------------------------------------
# generic matcher (synthetic fixtures, independent of the 15 real tasks)
# ---------------------------------------------------------------------------


def _synthetic_task() -> MCPUserTask:
    return MCPUserTask(
        id="synthetic",
        prompt="do step a then step b",
        servers=["_synthetic"],
        expected_calls=[
            ExpectedCall("step_a", {"key": "value"}),
            ExpectedCall("step_b", {}),
        ],
    )


def test_check_completion_true_for_exact_matching_trace() -> None:
    task = _synthetic_task()
    trace = [_call("step_a", {"key": "value"}), _call("step_b")]

    assert check_completion(task, trace) is True


def test_check_completion_true_with_extra_calls_interspersed() -> None:
    task = _synthetic_task()
    trace = [
        _call("unrelated_lookup"),
        _call("step_a", {"key": "value"}),
        _call("another_unrelated_call"),
        _call("step_b"),
    ]

    assert check_completion(task, trace) is True


def test_check_completion_false_for_wrong_tool() -> None:
    task = _synthetic_task()
    trace = [_call("step_a_typo", {"key": "value"}), _call("step_b")]

    assert check_completion(task, trace) is False


def test_check_completion_false_for_wrong_argument() -> None:
    task = _synthetic_task()
    trace = [_call("step_a", {"key": "wrong_value"}), _call("step_b")]

    assert check_completion(task, trace) is False


def test_check_completion_false_for_missing_step() -> None:
    task = _synthetic_task()
    trace = [_call("step_a", {"key": "value"})]

    assert check_completion(task, trace) is False


def test_check_completion_false_for_reordered_steps() -> None:
    task = _synthetic_task()
    trace = [_call("step_b"), _call("step_a", {"key": "value"})]

    assert check_completion(task, trace) is False


def test_check_completion_supports_callable_argument_predicate() -> None:
    task = MCPUserTask(
        id="synthetic_predicate",
        prompt="submit a positive amount",
        servers=["_synthetic"],
        expected_calls=[ExpectedCall("submit_amount", {"amount": lambda v: v > 0})],
    )

    assert check_completion(task, [_call("submit_amount", {"amount": 50})]) is True
    assert check_completion(task, [_call("submit_amount", {"amount": -5})]) is False


# ---------------------------------------------------------------------------
# the 15 real benign tasks
# ---------------------------------------------------------------------------


def test_exactly_fifteen_benign_tasks_are_authored() -> None:
    assert len(BENIGN_TASKS) == 15


def test_every_task_has_a_unique_id() -> None:
    ids = [task.id for task in BENIGN_TASKS]
    assert len(ids) == len(set(ids))


def test_every_task_requires_at_least_two_calls_across_at_least_one_server() -> None:
    for task in BENIGN_TASKS:
        assert len(task.expected_calls) >= 2, f"{task.id}: fewer than 2 expected calls"
        assert len(task.servers) >= 1, f"{task.id}: mounts no servers"


@pytest.mark.parametrize("task", BENIGN_TASKS, ids=[t.id for t in BENIGN_TASKS])
def test_check_completion_true_on_hand_built_correct_trace(task: MCPUserTask) -> None:
    assert check_completion(task, _trace_for(task)) is True


@pytest.mark.parametrize("task", BENIGN_TASKS, ids=[t.id for t in BENIGN_TASKS])
def test_check_completion_false_when_missing_last_step(task: MCPUserTask) -> None:
    incomplete_trace = _trace_for(task)[:-1]

    assert check_completion(task, incomplete_trace) is False


# ---------------------------------------------------------------------------
# end-to-end: replay each task's correct trace against the real S3-02 servers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("task", BENIGN_TASKS, ids=[t.id for t in BENIGN_TASKS])
def test_every_task_completes_successfully_against_the_real_servers(task: MCPUserTask) -> None:
    servers = [load_server(SERVERS_DIR / f"{name}.yaml") for name in task.servers]
    runtime = MockMCPRuntime(servers=servers)

    recorded: list[ToolCall] = []
    for expected in task.expected_calls:
        call = _call(expected.tool_name, dict(expected.arguments))
        result = runtime.call_tool(tool_call_id=call.id, name=call.name, arguments=call.arguments)
        assert result.is_error is False, (
            f"{task.id}: call to {call.name!r} with {call.arguments!r} failed: {result.content}"
        )
        recorded.append(call)

    assert check_completion(task, recorded) is True
