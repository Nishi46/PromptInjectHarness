import sqlite3
from pathlib import Path
from typing import Any

from injection_pareto.adapters import EpisodeResult
from injection_pareto.clients.base import ModelClient, ModelRequest, ModelResponse
from injection_pareto.config.schema import ExperimentConfig, ModelSpec, OutputConfig
from injection_pareto.defenses.stack import DefenseStack
from injection_pareto.sweep import run_sweep
from injection_pareto.sweep.runner import _config_hash
from injection_pareto.trace import connect, init_db, insert_episode, insert_run


class _FakeClient:
    cache_model_id = "fake"

    def generate(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError("no real model call should happen in this test")


def _fake_build_client(spec: ModelSpec, *, cache: Any, no_cache: bool = False) -> ModelClient:
    return _FakeClient()


def _make_config(trace_db: Path) -> ExperimentConfig:
    return ExperimentConfig(
        name="resume-test",
        models=[ModelSpec(id="L1", provider="ollama", model="llama3.2:3b")],
        defenses=["no_defense"],
        suites=["workspace"],
        attacks=[None],
        output=OutputConfig(trace_db=str(trace_db)),
        tasks={"workspace": ["user_task_0", "user_task_1"]},
        injection_tasks={},
    )


def test_resume_only_executes_missing_points(tmp_path: Path) -> None:
    trace_db = tmp_path / "trace.db"
    config = _make_config(trace_db)
    config_hash = _config_hash(config)

    # Simulate a prior partial sweep: user_task_0 already completed, under a
    # `run` row matching this exact config_hash/model/defense/suite/attack.
    conn = connect(trace_db)
    init_db(conn)
    run_id = insert_run(
        conn,
        config_hash=config_hash,
        model="llama3.2:3b",
        defense_stack="no_defense",
        suite="workspace",
        attack=None,
        started_at="t0",
    )
    insert_episode(conn, run_id=run_id, task_id="user_task_0", started_at="t0", utility=True)
    conn.close()

    calls: list[str] = []

    def _fake_run_episode(
        *,
        conn: sqlite3.Connection,
        run_id: int,
        suite_name: str,
        user_task_id: str,
        injection_task_id: str | None,
        model_client: ModelClient,
        defense_stack: DefenseStack,
        defense_name: str,
        model_name: str,
        attack_name: str | None = None,
    ) -> EpisodeResult:
        calls.append(user_task_id)
        episode_id = insert_episode(
            conn,
            run_id=run_id,
            task_id=user_task_id,
            injection_task_id=injection_task_id,
            started_at="t1",
            utility=True,
        )
        return EpisodeResult(
            episode_id=episode_id, utility=True, security=True, step_count=1, tool_call_count=0
        )

    summary = run_sweep(
        config,
        concurrency=1,
        show_progress=False,
        cache_dir=tmp_path / "cache",
        run_episode_fn=_fake_run_episode,
        build_client_fn=_fake_build_client,
    )

    assert calls == ["user_task_1"]
    assert summary.total_points == 2
    assert summary.already_done == 1
    assert summary.completed == 1
    assert summary.failed == 0


def test_second_invocation_resumes_from_the_first(tmp_path: Path) -> None:
    """A full end-to-end resume: run the sweep once (nothing pre-inserted),
    then run it again against the same trace DB and config -- the second
    invocation must find everything already done."""
    trace_db = tmp_path / "trace.db"
    config = _make_config(trace_db)
    calls: list[str] = []

    def _fake_run_episode(
        *,
        conn: sqlite3.Connection,
        run_id: int,
        suite_name: str,
        user_task_id: str,
        injection_task_id: str | None,
        model_client: ModelClient,
        defense_stack: DefenseStack,
        defense_name: str,
        model_name: str,
        attack_name: str | None = None,
    ) -> EpisodeResult:
        calls.append(user_task_id)
        episode_id = insert_episode(
            conn,
            run_id=run_id,
            task_id=user_task_id,
            injection_task_id=injection_task_id,
            started_at="t1",
            utility=True,
        )
        return EpisodeResult(
            episode_id=episode_id, utility=True, security=True, step_count=1, tool_call_count=0
        )

    first = run_sweep(
        config,
        concurrency=1,
        show_progress=False,
        cache_dir=tmp_path / "cache",
        run_episode_fn=_fake_run_episode,
        build_client_fn=_fake_build_client,
    )
    second = run_sweep(
        config,
        concurrency=1,
        show_progress=False,
        cache_dir=tmp_path / "cache",
        run_episode_fn=_fake_run_episode,
        build_client_fn=_fake_build_client,
    )

    assert sorted(calls) == ["user_task_0", "user_task_1"]  # each task ran exactly once
    assert first.completed == 2
    assert second.completed == 0
    assert second.already_done == 2


def test_a_failing_point_does_not_abort_the_rest_of_the_sweep(tmp_path: Path) -> None:
    trace_db = tmp_path / "trace.db"
    config = _make_config(trace_db)

    def _flaky_run_episode(
        *,
        conn: sqlite3.Connection,
        run_id: int,
        suite_name: str,
        user_task_id: str,
        injection_task_id: str | None,
        model_client: ModelClient,
        defense_stack: DefenseStack,
        defense_name: str,
        model_name: str,
        attack_name: str | None = None,
    ) -> EpisodeResult:
        if user_task_id == "user_task_0":
            raise RuntimeError("simulated transient failure")
        episode_id = insert_episode(
            conn, run_id=run_id, task_id=user_task_id, started_at="t1", utility=True
        )
        return EpisodeResult(
            episode_id=episode_id, utility=True, security=True, step_count=1, tool_call_count=0
        )

    summary = run_sweep(
        config,
        concurrency=1,
        show_progress=False,
        cache_dir=tmp_path / "cache",
        run_episode_fn=_flaky_run_episode,
        build_client_fn=_fake_build_client,
    )

    assert summary.completed == 1
    assert summary.failed == 1
    assert summary.failures[0].task == "user_task_0"
    assert "simulated transient failure" in summary.failures[0].error
