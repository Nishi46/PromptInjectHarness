from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from agentdojo.attacks.attack_registry import load_attack
from agentdojo.task_suite.load_suites import get_suite
from tqdm import tqdm

from injection_pareto.adapters import BENCHMARK_VERSION, run_episode, run_mcp_episode
from injection_pareto.adaptive.trial import (
    ADAPTIVE_ROUND_BUDGET,
    AdaptiveTrialResult,
    run_adaptive_trial,
)
from injection_pareto.attacks.registry import resolve_attack_name
from injection_pareto.cache import ResponseCache
from injection_pareto.clients.base import ModelClient
from injection_pareto.clients.factory import build_model_client
from injection_pareto.config.loader import expand_run_specs
from injection_pareto.config.schema import ExperimentConfig, ModelSpec
from injection_pareto.defenses import resolve_defense
from injection_pareto.defenses.stack import DefenseStack
from injection_pareto.mcp.poisoned import get_case
from injection_pareto.trace.db import connect, insert_run, open_db

# Same reasoning as `sweep/runner.py`'s identical constant (Appendix A.2/A.3):
# local inference is GPU-serialized so >1-2 concurrent Ollama calls doesn't
# help, and hosted free tiers stay conservative by default.
_DEFAULT_PROVIDER_CONCURRENCY = {"ollama": 2, "groq": 2}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _config_hash(config: ExperimentConfig) -> str:
    """Same construction as `sweep/runner.py::_config_hash` -- deliberately
    NOT shared code (that function is private to its module): identifies
    "this exact experiment" for resumability, forcing a fresh run rather
    than silently reusing trials computed under different settings."""
    payload = json.dumps(dataclasses.asdict(config), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


class _FakePipeline:
    """Just enough of a `BasePipelineElement` for `load_attack` to resolve a
    victim-model name off `.name` -- only `important_instructions` actually
    reads it (eagerly, in its own `__init__`). Mirrors the real
    `pipeline.name` assignment in `agentdojo_adapter.run_episode` and
    `tests/test_attack_families.py`'s identical `_FakePipeline`."""

    def __init__(self, name: str) -> None:
        self.name = name


@dataclass
class AdaptiveTrialPoint:
    """One `(model, defense, attack_family, task)` unit of adaptive-sweep
    work -- the trial-level analogue of `sweep/runner.py::_Point`. Carries
    everything `run_adaptive_trial` needs to start round 1: `goal` and
    `base_payload` are always plain strings regardless of suite (mirrors
    `adaptive/mutator.py`'s suite-agnostic interface); `injection_points` is
    only meaningful for a non-mcp suite, `poisoned_case_id` only for `mcp`."""

    model: ModelSpec
    defense: str
    suite: str
    attack_family: str
    task_id: str
    goal: str
    base_payload: str
    injection_points: tuple[str, ...] = ()
    injection_task_id: str | None = None
    poisoned_case_id: str | None = None


def _agentdojo_base_payload(
    *,
    suite_name: str,
    task_id: str,
    injection_task_id: str,
    attack_family: str,
    model_name: str,
    defense_name: str,
) -> tuple[str, str, tuple[str, ...]]:
    """Computes round 1's `(goal, base_payload, injection_points)` for an
    AgentDojo-suite point by calling the real, registered attack once --
    exactly what `run_episode` would do internally without an override
    (S4-03's "round 1 is deliberately identical to the existing static
    ASR@1 baseline"). No model call: `attack.attack()` is pure string
    formatting (or, for `encoding_obfuscation`, pure base64 encoding) plus
    AgentDojo's own local ground-truth pipeline for `get_injection_candidates`
    -- confirmed by `tests/test_attack_families.py`'s identical claim."""
    suite = get_suite(BENCHMARK_VERSION, suite_name)
    user_task = suite.get_user_task_by_id(task_id)
    injection_task = suite.get_injection_task_by_id(injection_task_id)
    pipeline = _FakePipeline(f"local-{model_name}-{defense_name}")
    attack = load_attack(resolve_attack_name(attack_family), suite, pipeline)
    injections = attack.attack(user_task, injection_task)
    injection_points = tuple(injections.keys())
    base_payload = next(iter(injections.values()))
    return injection_task.GOAL, base_payload, injection_points


def _mcp_base_payload(poisoned_case_id: str) -> tuple[str, str]:
    case = get_case(poisoned_case_id)
    return case.injection_goal, case.injection_text


def expand_adaptive_trial_points(config: ExperimentConfig) -> list[AdaptiveTrialPoint]:
    """Flattens `config` into one `AdaptiveTrialPoint` per `(model, defense,
    attack_family, task)`, reusing `config/loader.py::expand_run_specs`
    exactly as-is (S4-05's design decision: an adaptive config is an
    ordinary `ExperimentConfig`, just driven by `run_adaptive_trial`
    instead of one episode per point). A benign point (`attack is None`) has
    no adaptive trial to run and is skipped -- an adaptive sweep's config
    should declare only real attack-family names in `attacks:`, not `null`;
    reuse an existing static/mcp sweep's trace DB for the benign utility
    baseline instead of duplicating it here."""
    points: list[AdaptiveTrialPoint] = []
    for spec in expand_run_specs(config):
        if spec.attack is None:
            continue
        for task_id in spec.tasks:
            if spec.suite == "mcp":
                assert spec.injection_task is not None
                goal, base_payload = _mcp_base_payload(spec.injection_task)
                points.append(
                    AdaptiveTrialPoint(
                        model=spec.model,
                        defense=spec.defense,
                        suite=spec.suite,
                        attack_family=spec.attack,
                        task_id=task_id,
                        goal=goal,
                        base_payload=base_payload,
                        poisoned_case_id=spec.injection_task,
                    )
                )
            else:
                assert spec.injection_task is not None
                goal, base_payload, injection_points = _agentdojo_base_payload(
                    suite_name=spec.suite,
                    task_id=task_id,
                    injection_task_id=spec.injection_task,
                    attack_family=spec.attack,
                    model_name=spec.model.model,
                    defense_name=spec.defense,
                )
                points.append(
                    AdaptiveTrialPoint(
                        model=spec.model,
                        defense=spec.defense,
                        suite=spec.suite,
                        attack_family=spec.attack,
                        task_id=task_id,
                        goal=goal,
                        base_payload=base_payload,
                        injection_points=injection_points,
                        injection_task_id=spec.injection_task,
                    )
                )
    return points


def _subsample(
    points: list[AdaptiveTrialPoint], sample_fraction: float | None
) -> list[AdaptiveTrialPoint]:
    """Deterministic subsampling for the cost-risk mitigation S4-05's plan
    calls for: run ~1% of the grid, extrapolate the bill, decide whether to
    run the rest -- before spending real wall-clock on the full grid. Every
    `step`-th point, `step = round(1 / sample_fraction)`, so the same config
    always yields the same subsample (no RNG, no seed to track)."""
    if sample_fraction is None:
        return points
    if not 0 < sample_fraction <= 1:
        raise ValueError(f"sample_fraction must be in (0, 1], got {sample_fraction!r}")
    step = max(1, round(1 / sample_fraction))
    return points[::step]


@dataclass
class AdaptiveSweepFailure:
    suite: str
    task: str
    model: str
    defense: str
    attack: str
    error: str


@dataclass
class AdaptiveSweepSummary:
    total_trials: int
    already_done: int
    completed: int
    failed: int
    failures: list[AdaptiveSweepFailure] = field(default_factory=list)


def _find_or_create_run(
    conn: sqlite3.Connection,
    *,
    config_hash: str,
    model: str,
    defense: str,
    suite: str,
    attack: str,
) -> int:
    """Trial-sweep counterpart to `sweep/runner.py::_find_or_create_run` --
    not shared code, same reasoning: reuse an existing `run` row for this
    exact point across a resumed sweep instead of creating a duplicate."""
    row = conn.execute(
        "SELECT id FROM run WHERE config_hash = ? AND model = ? AND defense_stack = ? "
        "AND suite = ? AND attack IS ?",
        (config_hash, model, defense, suite, attack),
    ).fetchone()
    if row is not None:
        return int(row["id"])
    return insert_run(
        conn,
        config_hash=config_hash,
        model=model,
        defense_stack=defense,
        suite=suite,
        attack=attack,
        started_at=_now(),
    )


def _completed_trial_exists(
    conn: sqlite3.Connection, *, run_id: int, task_id: str, defense: str, attack_family: str
) -> bool:
    """A trial only ever gets `ended_at` set once, at the very end of
    `run_adaptive_trial` (mirrors `sweep/runner.py::_pending_points`'s
    "existence alone means done" reasoning for episodes) -- an interrupted
    trial's row (if any) has `ended_at IS NULL` and is treated as not done,
    so resuming re-runs it from round 1 (S4-03's documented non-resumability
    within a single trial; this is resumability *across* trials only)."""
    row = conn.execute(
        "SELECT 1 FROM adaptive_trial WHERE run_id = ? AND task_id = ? AND defense = ? "
        "AND attack_family = ? AND ended_at IS NOT NULL LIMIT 1",
        (run_id, task_id, defense, attack_family),
    ).fetchone()
    return row is not None


def _run_trial_point(
    point: AdaptiveTrialPoint,
    run_id: int,
    *,
    trace_db_path: str,
    cache: ResponseCache,
    no_cache: bool,
    provider_semaphores: dict[str, threading.Semaphore],
    build_client_fn: Callable[..., ModelClient],
    run_adaptive_trial_fn: Callable[..., AdaptiveTrialResult],
    budget: int,
) -> tuple[AdaptiveTrialPoint, Exception | None]:
    """Runs exactly one trial. Returns `(point, None)` on success or
    `(point, exception)` on failure -- never raises, so one bad point can't
    take down the rest of the sweep (mirrors `sweep/runner.py::_run_point`).
    Each call opens its own SQLite connection for thread-safety, same
    reasoning as the non-adaptive sweep runner."""
    semaphore = provider_semaphores.get(point.model.provider)
    if semaphore is not None:
        semaphore.acquire()
    try:
        conn = connect(trace_db_path)
        try:
            client = build_client_fn(point.model, cache=cache, no_cache=no_cache)

            # Safe as a plain closure (not defined inside a loop over
            # multiple points) -- `point` is fixed for the duration of this
            # call. A *fresh* stack per round (not a single shared instance)
            # is `run_adaptive_trial`'s own responsibility -- it calls this
            # factory once per round, not once per trial.
            def defense_stack_factory() -> DefenseStack:
                return DefenseStack([resolve_defense(point.defense)])

            common_kwargs: dict[str, object] = dict(
                conn=conn,
                run_id=run_id,
                suite=point.suite,
                task_id=point.task_id,
                defense_name=point.defense,
                attack_family=point.attack_family,
                goal=point.goal,
                base_payload=point.base_payload,
                user_task_id=point.task_id,
                model_client=client,
                model_name=point.model.model,
                defense_stack_factory=defense_stack_factory,
                budget=budget,
            )
            if point.suite == "mcp":
                run_adaptive_trial_fn(
                    **common_kwargs,
                    poisoned_case_id=point.poisoned_case_id,
                    run_mcp_episode_fn=run_mcp_episode,
                )
            else:
                run_adaptive_trial_fn(
                    **common_kwargs,
                    injection_task_id=point.injection_task_id,
                    injection_points=point.injection_points,
                    run_episode_fn=run_episode,
                )
            return point, None
        finally:
            conn.close()
    except Exception as exc:  # deliberately broad: isolate one point's failure from the rest
        return point, exc
    finally:
        if semaphore is not None:
            semaphore.release()


def run_adaptive_sweep(
    config: ExperimentConfig,
    *,
    concurrency: int = 4,
    provider_concurrency: dict[str, int] | None = None,
    no_cache: bool = False,
    cache_dir: str | Path = ".cache/responses",
    show_progress: bool = True,
    sample_fraction: float | None = None,
    budget: int = ADAPTIVE_ROUND_BUDGET,
    run_adaptive_trial_fn: Callable[..., AdaptiveTrialResult] = run_adaptive_trial,
    build_client_fn: Callable[..., ModelClient] = build_model_client,
) -> AdaptiveSweepSummary:
    """Drives every `AdaptiveTrialPoint` in `config` (`expand_adaptive_trial_points`,
    optionally subsampled via `sample_fraction`) through `run_adaptive_trial_fn`,
    skipping points with an already-completed `adaptive_trial` row (resumable
    across process restarts at trial granularity -- see
    `_completed_trial_exists`'s docstring for what that does and doesn't
    cover) and bounding concurrency both overall (`concurrency`) and per
    model provider (`provider_concurrency`). Mirrors
    `sweep/runner.py::run_sweep`'s shape deliberately; the unit of work is
    one trial (up to `budget` episodes) instead of one episode.
    """
    trial_points = _subsample(expand_adaptive_trial_points(config), sample_fraction)
    config_hash = _config_hash(config)
    trace_db_path = Path(config.output.trace_db)
    trace_db_path.parent.mkdir(parents=True, exist_ok=True)

    with open_db(trace_db_path) as conn:
        resolved = [
            (
                point,
                _find_or_create_run(
                    conn,
                    config_hash=config_hash,
                    model=point.model.model,
                    defense=point.defense,
                    suite=point.suite,
                    attack=point.attack_family,
                ),
            )
            for point in trial_points
        ]
        pending = [
            (point, run_id)
            for point, run_id in resolved
            if not _completed_trial_exists(
                conn,
                run_id=run_id,
                task_id=point.task_id,
                defense=point.defense,
                attack_family=point.attack_family,
            )
        ]

    total = len(trial_points)
    already_done = total - len(pending)

    cache = ResponseCache(cache_dir)
    limits = {**_DEFAULT_PROVIDER_CONCURRENCY, **(provider_concurrency or {})}
    semaphores = {provider: threading.Semaphore(limit) for provider, limit in limits.items()}

    completed = 0
    failures: list[AdaptiveSweepFailure] = []
    progress = tqdm(total=len(pending), desc=config.name, disable=not show_progress)
    try:
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            futures = [
                pool.submit(
                    _run_trial_point,
                    point,
                    run_id,
                    trace_db_path=str(trace_db_path),
                    cache=cache,
                    no_cache=no_cache,
                    provider_semaphores=semaphores,
                    build_client_fn=build_client_fn,
                    run_adaptive_trial_fn=run_adaptive_trial_fn,
                    budget=budget,
                )
                for point, run_id in pending
            ]
            for future in as_completed(futures):
                point, error = future.result()
                if error is None:
                    completed += 1
                else:
                    failures.append(
                        AdaptiveSweepFailure(
                            suite=point.suite,
                            task=point.task_id,
                            model=point.model.model,
                            defense=point.defense,
                            attack=point.attack_family,
                            error=str(error),
                        )
                    )
                progress.update(1)
    finally:
        progress.close()

    return AdaptiveSweepSummary(
        total_trials=total,
        already_done=already_done,
        completed=completed,
        failed=len(failures),
        failures=failures,
    )
