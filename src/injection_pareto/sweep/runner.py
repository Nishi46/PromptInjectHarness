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

from tqdm import tqdm

from injection_pareto.adapters import EpisodeResult, run_episode
from injection_pareto.cache import ResponseCache
from injection_pareto.clients.base import ModelClient
from injection_pareto.clients.factory import build_model_client
from injection_pareto.config.loader import expand_run_specs
from injection_pareto.config.schema import ExperimentConfig, RunSpec
from injection_pareto.defenses import resolve_defense
from injection_pareto.defenses.stack import DefenseStack
from injection_pareto.trace.db import connect, insert_run, open_db

# Local inference is GPU-serialized anyway (Appendix A.2), so more than one
# or two concurrent Ollama calls doesn't help and can slow things down;
# hosted free tiers stay conservative by default (Appendix A.3's caps bind
# on daily volume, not concurrency, but a low concurrent-request ceiling is
# still the polite default). `run_sweep`'s `provider_concurrency` overrides
# these per config.
_DEFAULT_PROVIDER_CONCURRENCY = {"ollama": 2, "groq": 2}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _config_hash(config: ExperimentConfig) -> str:
    """Identifies "this exact experiment" for resumability -- changes if
    anything about the config changes (models, defenses, tasks, ...),
    intentionally forcing a fresh (non-resumed) run rather than silently
    reusing episodes computed under different settings."""
    payload = json.dumps(dataclasses.asdict(config), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class _Point:
    """One fully-resolved unit of work: a `RunSpec` (model/defense/suite/
    attack) plus one of its tasks, plus the `run_id` it belongs under."""

    spec: RunSpec
    task: str
    run_id: int


@dataclass
class SweepFailure:
    suite: str
    task: str
    model: str
    defense: str
    attack: str | None
    error: str


@dataclass
class SweepSummary:
    total_points: int
    already_done: int
    completed: int
    failed: int
    failures: list[SweepFailure] = field(default_factory=list)


def _find_or_create_run(
    conn: sqlite3.Connection,
    *,
    config_hash: str,
    model: str,
    defense: str,
    suite: str,
    attack: str | None,
) -> int:
    """Reuses an existing `run` row for this exact (config_hash, model,
    defense, suite, attack) point if one already exists -- so resuming a
    sweep after a partial failure appends missing episodes to the same
    logical run instead of creating a duplicate `run` row for the same
    point. Resolved once per `RunSpec`, single-threaded, before any worker
    starts, so concurrent workers never race to create the same row."""
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


def _pending_points(conn: sqlite3.Connection, points: list[_Point]) -> list[_Point]:
    """Filters `points` down to those without an already-completed matching
    episode. An episode row only ever exists fully-formed (the adapter
    inserts it once, after the episode finishes, with `utility`/`security`
    already known -- no partial/in-progress rows), so existence alone means
    "done"."""
    pending = []
    for point in points:
        row = conn.execute(
            "SELECT 1 FROM episode WHERE run_id = ? AND task_id = ? "
            "AND injection_task_id IS ? LIMIT 1",
            (point.run_id, point.task, point.spec.injection_task),
        ).fetchone()
        if row is None:
            pending.append(point)
    return pending


def _run_point(
    point: _Point,
    *,
    trace_db_path: str,
    cache: ResponseCache,
    no_cache: bool,
    provider_semaphores: dict[str, threading.Semaphore],
    build_client_fn: Callable[..., ModelClient],
    run_episode_fn: Callable[..., EpisodeResult],
) -> tuple[_Point, Exception | None]:
    """Runs exactly one episode. Returns `(point, None)` on success or
    `(point, exception)` on failure -- never raises, so one bad point (a
    transient API error, a malformed model response, ...) can't take down
    the rest of the sweep. Each call opens its own SQLite connection
    (required for thread-safety; the schema's WAL mode + busy_timeout,
    S1-03, exist specifically so concurrent connections can write without
    serializing on the rollback journal)."""
    semaphore = provider_semaphores.get(point.spec.model.provider)
    if semaphore is not None:
        semaphore.acquire()
    try:
        conn = connect(trace_db_path)
        try:
            client = build_client_fn(point.spec.model, cache=cache, no_cache=no_cache)
            defense_stack = DefenseStack([resolve_defense(point.spec.defense)])
            run_episode_fn(
                conn=conn,
                run_id=point.run_id,
                suite_name=point.spec.suite,
                user_task_id=point.task,
                injection_task_id=point.spec.injection_task,
                model_client=client,
                defense_stack=defense_stack,
                defense_name=point.spec.defense,
                model_name=point.spec.model.model,
                attack_name=point.spec.attack,
            )
            return point, None
        finally:
            conn.close()
    except Exception as exc:  # deliberately broad: isolate one point's failure from the rest
        return point, exc
    finally:
        if semaphore is not None:
            semaphore.release()


def run_sweep(
    config: ExperimentConfig,
    *,
    concurrency: int = 4,
    provider_concurrency: dict[str, int] | None = None,
    no_cache: bool = False,
    cache_dir: str | Path = ".cache/responses",
    show_progress: bool = True,
    run_episode_fn: Callable[..., EpisodeResult] = run_episode,
    build_client_fn: Callable[..., ModelClient] = build_model_client,
) -> SweepSummary:
    """Drives every point in `config`'s `models × defenses × suites ×
    attacks × tasks` grid through `run_episode_fn`, skipping points that
    already have a matching episode in the trace DB (resumable across
    process restarts) and bounding concurrency both overall (`concurrency`)
    and per model provider (`provider_concurrency`, Appendix A.3's rate
    limits). `run_episode_fn`/`build_client_fn` are injectable so tests can
    substitute fakes without real model calls.
    """
    run_specs = expand_run_specs(config)
    config_hash = _config_hash(config)
    trace_db_path = Path(config.output.trace_db)
    trace_db_path.parent.mkdir(parents=True, exist_ok=True)

    with open_db(trace_db_path) as conn:
        run_ids = [
            _find_or_create_run(
                conn,
                config_hash=config_hash,
                model=spec.model.model,
                defense=spec.defense,
                suite=spec.suite,
                attack=spec.attack,
            )
            for spec in run_specs
        ]
        points = [
            _Point(spec=spec, task=task, run_id=run_id)
            for spec, run_id in zip(run_specs, run_ids, strict=True)
            for task in spec.tasks
        ]
        pending = _pending_points(conn, points)

    total = len(points)
    already_done = total - len(pending)

    cache = ResponseCache(cache_dir)
    limits = {**_DEFAULT_PROVIDER_CONCURRENCY, **(provider_concurrency or {})}
    semaphores = {provider: threading.Semaphore(limit) for provider, limit in limits.items()}

    completed = 0
    failures: list[SweepFailure] = []
    progress = tqdm(total=len(pending), desc=config.name, disable=not show_progress)
    try:
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            futures = [
                pool.submit(
                    _run_point,
                    point,
                    trace_db_path=str(trace_db_path),
                    cache=cache,
                    no_cache=no_cache,
                    provider_semaphores=semaphores,
                    build_client_fn=build_client_fn,
                    run_episode_fn=run_episode_fn,
                )
                for point in pending
            ]
            for future in as_completed(futures):
                point, error = future.result()
                if error is None:
                    completed += 1
                else:
                    failures.append(
                        SweepFailure(
                            suite=point.spec.suite,
                            task=point.task,
                            model=point.spec.model.model,
                            defense=point.spec.defense,
                            attack=point.spec.attack,
                            error=str(error),
                        )
                    )
                progress.update(1)
    finally:
        progress.close()

    return SweepSummary(
        total_points=total,
        already_done=already_done,
        completed=completed,
        failed=len(failures),
        failures=failures,
    )
