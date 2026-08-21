"""S7-05: unit tests for `scripts/export_mcp_dataset.py`'s own new logic
-- the callable-predicate serialization and the server-to-benign-task
pairing validation. Everything else the script does (`PoisonedCase.apply`,
`load_named_server`, the real case/task tables) is already exercised by
`tests/test_mcp_poisoned.py`/`test_mcp_tasks.py`, so this file doesn't
re-test it. `scripts/` isn't an importable package, so this loads the
module directly from its file path, the same technique
`test_composition_results.py` uses."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "export_mcp_dataset.py"
_spec = importlib.util.spec_from_file_location("export_mcp_dataset", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
export_mcp_dataset = importlib.util.module_from_spec(_spec)
sys.modules["export_mcp_dataset"] = export_mcp_dataset
_spec.loader.exec_module(export_mcp_dataset)

_serialize_argument = export_mcp_dataset._serialize_argument
_serialize_expected_call = export_mcp_dataset._serialize_expected_call
_benign_task_by_server = export_mcp_dataset._benign_task_by_server
MCPUserTask = export_mcp_dataset.MCPUserTask


def test_serialize_argument_passes_through_json_plain_literals() -> None:
    assert _serialize_argument("attacker@evil.example") == "attacker@evil.example"
    assert _serialize_argument(5000) == 5000
    assert _serialize_argument(["a@b.com"]) == ["a@b.com"]


def test_serialize_argument_represents_a_callable_predicate_as_its_own_source() -> None:
    result = _serialize_argument(lambda v: "needle" in v)

    assert isinstance(result, dict)
    assert set(result) == {"__predicate__"}
    assert "needle" in result["__predicate__"]
    assert "lambda" in result["__predicate__"]


def test_serialize_argument_predicate_is_json_serializable() -> None:
    """The whole point: a raw callable would blow up `json.dumps` -- the
    serialized form must not."""
    result = _serialize_argument(lambda v: True)

    json.dumps(result)  # must not raise


def test_serialize_expected_call_shape() -> None:
    result = _serialize_expected_call("send_email", {"to": "x@example.com"})

    assert result == {"tool_name": "send_email", "arguments": {"to": "x@example.com"}}


def test_benign_task_by_server_maps_each_single_server_task() -> None:
    tasks = [
        MCPUserTask(id="t1", prompt="p1", servers=["email"], expected_calls=[]),
        MCPUserTask(id="t2", prompt="p2", servers=["crm"], expected_calls=[]),
    ]

    # `_benign_task_by_server` takes no arguments in the real script (it
    # reads the module-level `BENIGN_TASKS` directly) -- verified here by
    # monkeypatching that constant (via `setattr`/`getattr` rather than
    # dotted attribute syntax, since mypy doesn't know this dynamically
    # `importlib`-loaded module has a `BENIGN_TASKS` attribute).
    original = getattr(export_mcp_dataset, "BENIGN_TASKS")
    try:
        setattr(export_mcp_dataset, "BENIGN_TASKS", tasks)
        by_server = _benign_task_by_server()
    finally:
        setattr(export_mcp_dataset, "BENIGN_TASKS", original)

    assert by_server == {"email": tasks[0], "crm": tasks[1]}


def test_benign_task_by_server_rejects_a_multi_server_task() -> None:
    """This export's pairing assumes exactly one server per benign task --
    must fail loudly, not silently pick one, if that ever stops holding."""
    tasks = [MCPUserTask(id="t1", prompt="p1", servers=["email", "crm"], expected_calls=[])]

    original = getattr(export_mcp_dataset, "BENIGN_TASKS")
    try:
        setattr(export_mcp_dataset, "BENIGN_TASKS", tasks)
        with pytest.raises(ValueError, match="t1"):
            _benign_task_by_server()
    finally:
        setattr(export_mcp_dataset, "BENIGN_TASKS", original)


def test_real_case_set_exports_all_41_with_every_case_paired() -> None:
    """Pinned to this project's real, live data (not a fixture) -- the
    export script's own acceptance bar: every real `PoisonedCase` must
    resolve a ground truth and a paired benign task, or the script itself
    raises rather than silently dropping one."""
    benign_by_server = _benign_task_by_server()
    injection_by_case_id = export_mcp_dataset._injection_task_by_case_id()

    assert len(export_mcp_dataset.POISONED_CASES) == 41
    for case in export_mcp_dataset.POISONED_CASES:
        assert case.id in injection_by_case_id
        assert case.target_server in benign_by_server
