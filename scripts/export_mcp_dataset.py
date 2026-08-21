"""S7-05 -- exports the real 41 `PoisonedCase` entries (`mcp/poisoned.py`,
S3-04 + S5-04's one D8 demonstration case) plus each one's formal ground
truth (`mcp/injection_tasks.py`, S3-05) and paired benign task
(`mcp/tasks.py`, S3-03) into a single, self-contained JSONL file for
external release (S7-05) -- a script, not a one-off hand export, so it
regenerates automatically if the case set ever changes.

Every field is pulled from the real, live objects (`PoisonedCase.apply()`,
`load_named_server()`) rather than reimplemented -- the poisoned tool
description in the output is computed the exact same way the harness
itself computes it at episode-run time, not a second, potentially-drifting
copy of that logic.

Usage: .venv/bin/python scripts/export_mcp_dataset.py
"""

from __future__ import annotations

import inspect
import json
import types
from pathlib import Path
from typing import Any

from injection_pareto.mcp.injection_tasks import INJECTION_TASKS, MCPInjectionTask
from injection_pareto.mcp.loader import load_named_server
from injection_pareto.mcp.poisoned import POISONED_CASES, PoisonedCase
from injection_pareto.mcp.tasks import BENIGN_TASKS, MCPUserTask

OUTPUT_PATH = Path("dataset/mcp_poisoning_suite/mcp_poisoning_suite.jsonl")


def _serialize_argument(value: object) -> object:
    """`ExpectedCall.arguments` values are normally JSON-plain literals,
    but one real case (`poison_body_exfil_email_get_email`'s ground truth,
    S5-04) uses a callable predicate instead of an equality value --
    `json.dumps` can't serialize a function. Represented here as its own
    real source line (`inspect.getsource`), not a hand-written paraphrase
    that could drift from what the predicate actually checks."""
    if isinstance(value, types.FunctionType):
        try:
            source = inspect.getsource(value).strip().rstrip(",")
        except OSError:
            source = repr(value)
        return {"__predicate__": source}
    return value


def _serialize_expected_call(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "arguments": {key: _serialize_argument(value) for key, value in arguments.items()},
    }


def _benign_task_by_server() -> dict[str, MCPUserTask]:
    """One `MCPUserTask` per server (`mcp/tasks.py`'s own real 15-tasks/
    15-servers design, confirmed here rather than assumed -- raises if a
    future task ever names more than one server, since the 1:1 pairing
    this export relies on would no longer hold)."""
    by_server: dict[str, MCPUserTask] = {}
    for task in BENIGN_TASKS:
        if len(task.servers) != 1:
            raise ValueError(
                f"task {task.id!r} names {len(task.servers)} servers "
                f"({task.servers!r}) -- the poisoned-case pairing below assumes "
                "exactly one server per benign task"
            )
        by_server[task.servers[0]] = task
    return by_server


def _injection_task_by_case_id() -> dict[str, MCPInjectionTask]:
    return {task.poisoned_case.id: task for task in INJECTION_TASKS}


def _export_record(
    case: PoisonedCase,
    *,
    injection_task: MCPInjectionTask,
    paired_task: MCPUserTask | None,
) -> dict[str, Any]:
    server = load_named_server(case.target_server)
    tool = server.get_tool(case.target_tool)
    if tool is None:
        raise ValueError(f"server {case.target_server!r} has no tool {case.target_tool!r}")
    poisoned_server = case.apply(server)
    poisoned_tool = poisoned_server.get_tool(case.target_tool)
    assert poisoned_tool is not None

    return {
        "id": case.id,
        "sub_family": case.sub_family,
        "target_server": case.target_server,
        "target_tool": case.target_tool,
        "original_tool_description": tool.description,
        "injection_text": case.injection_text,
        "poisoned_tool_description": poisoned_tool.description,
        "injection_goal": case.injection_goal,
        "ground_truth_calls": [
            _serialize_expected_call(c.tool_name, c.arguments)
            for c in injection_task.ground_truth_calls
        ],
        "paired_benign_task": (
            {
                "id": paired_task.id,
                "prompt": paired_task.prompt,
                "expected_calls": [
                    _serialize_expected_call(c.tool_name, c.arguments)
                    for c in paired_task.expected_calls
                ],
            }
            if paired_task is not None
            else None
        ),
    }


def main() -> None:
    benign_by_server = _benign_task_by_server()
    injection_by_case_id = _injection_task_by_case_id()

    records = []
    for case in POISONED_CASES:
        injection_task = injection_by_case_id.get(case.id)
        if injection_task is None:
            raise ValueError(f"case {case.id!r} has no matching MCPInjectionTask ground truth")
        records.append(
            _export_record(
                case,
                injection_task=injection_task,
                paired_task=benign_by_server.get(case.target_server),
            )
        )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w") as f:
        for record in records:
            f.write(json.dumps(record, sort_keys=True) + "\n")

    n_missing_pair = sum(1 for r in records if r["paired_benign_task"] is None)
    print(f"wrote {OUTPUT_PATH} ({len(records)} records, {n_missing_pair} without a paired task)")


if __name__ == "__main__":
    main()
