from pathlib import Path
from typing import Any

import pytest

from injection_pareto.adaptive.sweep import (
    AdaptiveTrialPoint,
    _config_hash,
    _subsample,
    expand_adaptive_trial_points,
    run_adaptive_sweep,
)
from injection_pareto.adaptive.trial import ADAPTIVE_ROUND_BUDGET, AdaptiveTrialResult
from injection_pareto.clients.base import ModelClient, ModelRequest, ModelResponse
from injection_pareto.config.schema import ExperimentConfig, ModelSpec, OutputConfig
from injection_pareto.trace import connect, init_db, insert_adaptive_trial, insert_run

# Same known-good combo used throughout Sprint 1-3's tests: real, local, no
# network/model calls needed to load these.
_WORKSPACE_MODEL = ModelSpec(id="L1", provider="ollama", model="llama3.2:3b")
_MCP_TASK_ID = "mcp_file_storage_0"
_MCP_CASE_ID = "poison_direct_instruction_file_storage_list_files"


class _FakeClient:
    cache_model_id = "fake"

    def generate(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError("no real model call should happen in this test")


def _fake_build_client(spec: ModelSpec, *, cache: Any, no_cache: bool = False) -> ModelClient:
    return _FakeClient()


def _workspace_config(
    trace_db: Path,
    *,
    defenses: list[str] | None = None,
    attacks: list[str | None] | None = None,
    tasks: list[str] | None = None,
    models: list[ModelSpec] | None = None,
) -> ExperimentConfig:
    return ExperimentConfig(
        name="adaptive-test",
        models=models or [_WORKSPACE_MODEL],
        defenses=defenses or ["no_defense"],
        suites=["workspace"],
        attacks=attacks or ["naive"],
        output=OutputConfig(trace_db=str(trace_db)),
        tasks={"workspace": tasks or ["user_task_0"]},
        injection_tasks={"workspace": "injection_task_0"},
    )


def _mcp_config(trace_db: Path) -> ExperimentConfig:
    return ExperimentConfig(
        name="adaptive-mcp-test",
        models=[_WORKSPACE_MODEL],
        defenses=["no_defense"],
        suites=["mcp"],
        attacks=["direct_instruction"],
        output=OutputConfig(trace_db=str(trace_db)),
        tasks={},
        injection_tasks={},
        mcp_tasks=[_MCP_TASK_ID],
        mcp_poisoned_cases={"direct_instruction": _MCP_CASE_ID},
    )


# ---------------------------------------------------------------------------
# expand_adaptive_trial_points
# ---------------------------------------------------------------------------


def test_expand_skips_benign_points(tmp_path: Path) -> None:
    config = _workspace_config(tmp_path / "trace.db", attacks=[None, "naive"])
    points = expand_adaptive_trial_points(config)
    assert len(points) == 1
    assert points[0].attack_family == "naive"


def test_expand_computes_a_real_base_payload_and_injection_points(tmp_path: Path) -> None:
    config = _workspace_config(tmp_path / "trace.db", attacks=["naive"])
    points = expand_adaptive_trial_points(config)
    assert len(points) == 1
    point = points[0]
    assert point.base_payload.startswith("TODO: ")
    assert point.injection_points
    assert point.goal in point.base_payload
    assert point.injection_task_id == "injection_task_0"
    assert point.poisoned_case_id is None


def test_expand_multiplies_over_defenses_attacks_models_and_tasks(tmp_path: Path) -> None:
    config = _workspace_config(
        tmp_path / "trace.db",
        defenses=["no_defense", "spotlighting"],
        attacks=["naive", "ignore_previous"],
        tasks=["user_task_0", "user_task_1"],
        models=[_WORKSPACE_MODEL, ModelSpec(id="L2", provider="ollama", model="llama3.1:latest")],
    )
    points = expand_adaptive_trial_points(config)
    assert len(points) == 2 * 2 * 2 * 2  # defenses x attacks x models x tasks


def test_expand_mcp_suite_uses_the_poisoned_cases_own_payload(tmp_path: Path) -> None:
    config = _mcp_config(tmp_path / "trace.db")
    points = expand_adaptive_trial_points(config)
    assert len(points) == 1
    point = points[0]
    assert point.suite == "mcp"
    assert point.poisoned_case_id == _MCP_CASE_ID
    assert point.injection_points == ()
    assert point.base_payload  # PoisonedCase.injection_text, non-empty


# ---------------------------------------------------------------------------
# _subsample
# ---------------------------------------------------------------------------


def _dummy_points(n: int) -> list[AdaptiveTrialPoint]:
    return [
        AdaptiveTrialPoint(
            model=_WORKSPACE_MODEL,
            defense="no_defense",
            suite="workspace",
            attack_family="naive",
            task_id=f"task_{i}",
            goal="goal",
            base_payload="payload",
        )
        for i in range(n)
    ]


def test_subsample_none_returns_everything() -> None:
    points = _dummy_points(10)
    assert _subsample(points, None) == points


def test_subsample_is_deterministic_across_calls() -> None:
    points = _dummy_points(10)
    first = _subsample(points, 0.5)
    second = _subsample(points, 0.5)
    assert first == second
    assert len(first) == 5


def test_subsample_of_a_tiny_fraction_still_returns_at_least_one() -> None:
    points = _dummy_points(10)
    assert len(_subsample(points, 0.01)) >= 1


@pytest.mark.parametrize("bad_fraction", [0, -0.5, 1.5])
def test_subsample_rejects_out_of_range_fraction(bad_fraction: float) -> None:
    with pytest.raises(ValueError, match="sample_fraction"):
        _subsample(_dummy_points(10), bad_fraction)


# ---------------------------------------------------------------------------
# run_adaptive_sweep: dispatch, resumability, failure isolation
# ---------------------------------------------------------------------------


def _fake_run_adaptive_trial(calls: list[dict[str, Any]]) -> Any:
    def _fake(**kwargs: Any) -> AdaptiveTrialResult:
        calls.append(kwargs)
        return AdaptiveTrialResult(trial_id=1, rounds_run=1, success=False, rounds_to_success=None)

    return _fake


def test_run_adaptive_sweep_dispatches_agentdojo_points_with_run_episode_fn(
    tmp_path: Path,
) -> None:
    config = _workspace_config(tmp_path / "trace.db")
    calls: list[dict[str, Any]] = []

    summary = run_adaptive_sweep(
        config,
        build_client_fn=_fake_build_client,
        run_adaptive_trial_fn=_fake_run_adaptive_trial(calls),
        show_progress=False,
    )

    assert summary.total_trials == 1
    assert summary.completed == 1
    assert summary.already_done == 0
    assert summary.failed == 0
    assert len(calls) == 1
    call = calls[0]
    assert call["suite"] == "workspace"
    assert call["attack_family"] == "naive"
    assert call["injection_task_id"] == "injection_task_0"
    assert call["injection_points"]
    assert call["run_episode_fn"] is not None
    assert "poisoned_case_id" not in call


def test_run_adaptive_sweep_dispatches_mcp_points_with_run_mcp_episode_fn(
    tmp_path: Path,
) -> None:
    config = _mcp_config(tmp_path / "trace.db")
    calls: list[dict[str, Any]] = []

    summary = run_adaptive_sweep(
        config,
        build_client_fn=_fake_build_client,
        run_adaptive_trial_fn=_fake_run_adaptive_trial(calls),
        show_progress=False,
    )

    assert summary.completed == 1
    call = calls[0]
    assert call["suite"] == "mcp"
    assert call["poisoned_case_id"] == _MCP_CASE_ID
    assert call["run_mcp_episode_fn"] is not None
    assert "injection_task_id" not in call


def test_run_adaptive_sweep_honors_budget_override(tmp_path: Path) -> None:
    config = _workspace_config(tmp_path / "trace.db")
    calls: list[dict[str, Any]] = []

    run_adaptive_sweep(
        config,
        build_client_fn=_fake_build_client,
        run_adaptive_trial_fn=_fake_run_adaptive_trial(calls),
        show_progress=False,
        budget=3,
    )

    assert calls[0]["budget"] == 3


def test_run_adaptive_sweep_default_budget_is_the_module_constant(tmp_path: Path) -> None:
    config = _workspace_config(tmp_path / "trace.db")
    calls: list[dict[str, Any]] = []

    run_adaptive_sweep(
        config,
        build_client_fn=_fake_build_client,
        run_adaptive_trial_fn=_fake_run_adaptive_trial(calls),
        show_progress=False,
    )

    assert calls[0]["budget"] == ADAPTIVE_ROUND_BUDGET


def test_run_adaptive_sweep_sample_fraction_reduces_trial_count(tmp_path: Path) -> None:
    config = _workspace_config(tmp_path / "trace.db", tasks=["user_task_0", "user_task_1"])
    calls: list[dict[str, Any]] = []

    summary = run_adaptive_sweep(
        config,
        build_client_fn=_fake_build_client,
        run_adaptive_trial_fn=_fake_run_adaptive_trial(calls),
        show_progress=False,
        sample_fraction=0.5,
    )

    assert summary.total_trials == 1
    assert len(calls) == 1


def test_run_adaptive_sweep_resume_skips_a_completed_trial(tmp_path: Path) -> None:
    trace_db = tmp_path / "trace.db"
    config = _workspace_config(trace_db)
    config_hash = _config_hash(config)

    conn = connect(trace_db)
    init_db(conn)
    run_id = insert_run(
        conn,
        config_hash=config_hash,
        model="llama3.2:3b",
        defense_stack="no_defense",
        suite="workspace",
        attack="naive",
        started_at="t0",
    )
    trial_id = insert_adaptive_trial(
        conn,
        run_id=run_id,
        task_id="user_task_0",
        defense="no_defense",
        attack_family="naive",
        suite="workspace",
        budget=ADAPTIVE_ROUND_BUDGET,
        started_at="t0",
    )
    conn.execute(
        "UPDATE adaptive_trial SET ended_at = ? WHERE id = ?", ("t1", trial_id)
    )
    conn.commit()
    conn.close()

    calls: list[dict[str, Any]] = []
    summary = run_adaptive_sweep(
        config,
        build_client_fn=_fake_build_client,
        run_adaptive_trial_fn=_fake_run_adaptive_trial(calls),
        show_progress=False,
    )

    assert summary.total_trials == 1
    assert summary.already_done == 1
    assert summary.completed == 0
    assert calls == []


def test_run_adaptive_sweep_isolates_a_failing_trial(tmp_path: Path) -> None:
    config = _workspace_config(
        tmp_path / "trace.db", defenses=["no_defense", "spotlighting"]
    )

    def _flaky(**kwargs: Any) -> AdaptiveTrialResult:
        if kwargs["defense_name"] == "spotlighting":
            raise RuntimeError("boom")
        return AdaptiveTrialResult(trial_id=1, rounds_run=1, success=False, rounds_to_success=None)

    summary = run_adaptive_sweep(
        config,
        build_client_fn=_fake_build_client,
        run_adaptive_trial_fn=_flaky,
        show_progress=False,
    )

    assert summary.total_trials == 2
    assert summary.completed == 1
    assert summary.failed == 1
    assert summary.failures[0].defense == "spotlighting"
    assert "boom" in summary.failures[0].error
