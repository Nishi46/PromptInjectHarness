from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from injection_pareto.adaptive.feedback import EpisodeFeedback, extract_feedback
from injection_pareto.adaptive.mutator import mutate_payload
from injection_pareto.clients.base import ModelClient, ModelResponse
from injection_pareto.defenses.stack import DefenseStack
from injection_pareto.trace.db import (
    insert_adaptive_round,
    insert_adaptive_trial,
    insert_cost_record,
    transaction,
    update_adaptive_trial_result,
)

# The single source of truth for "N=20 rounds" -- S4-03's acceptance
# criterion is that every (defense, attack) pair gets exactly the same
# refinement budget. Every `configs/adaptive*.yaml` sweep (S4-05/S4-06)
# drives `run_adaptive_trial` at this value; it is never meant to vary
# between defenses/attacks, even though nothing here forbids a caller from
# passing a different `budget` (useful for a cheap smoke test) -- the
# "identical for every pair" guarantee lives in how the real sweeps call
# this function, not in the function refusing other values. See
# `tests/test_adaptive_trial.py` for the regression proving every trial
# records whatever `budget` was actually run.
ADAPTIVE_ROUND_BUDGET = 20


def _now() -> str:
    return datetime.now(UTC).isoformat()


class _EpisodeResultLike(Protocol):
    """Structural match for both `agentdojo_adapter.EpisodeResult` and
    `mcp_adapter.EpisodeResult` -- the only field this module needs off
    either one."""

    episode_id: int


@dataclass
class AdaptiveTrialResult:
    trial_id: int
    rounds_run: int
    success: bool
    rounds_to_success: int | None


def run_adaptive_trial(
    *,
    conn: sqlite3.Connection,
    run_id: int,
    suite: str,
    task_id: str,
    defense_name: str,
    attack_family: str,
    goal: str,
    base_payload: str,
    user_task_id: str,
    model_client: ModelClient,
    model_name: str,
    defense_stack_factory: Callable[[], DefenseStack],
    injection_points: tuple[str, ...] = (),
    injection_task_id: str | None = None,
    poisoned_case_id: str | None = None,
    run_episode_fn: Callable[..., _EpisodeResultLike] | None = None,
    run_mcp_episode_fn: Callable[..., _EpisodeResultLike] | None = None,
    extract_feedback_fn: Callable[..., EpisodeFeedback] = extract_feedback,
    mutate_payload_fn: Callable[..., str] = mutate_payload,
    budget: int = ADAPTIVE_ROUND_BUDGET,
) -> AdaptiveTrialResult:
    """Runs one adaptive trial: up to `budget` rounds of `(defense, task)`
    against `attack_family`, mutating the payload between rounds via
    `mutate_payload_fn` (S4-02) in response to `extract_feedback_fn`'s
    signal (S4-01). **This is S4-03's scope only** -- every round always
    runs, unconditionally, through `budget`; there is no early exit on
    success yet (S4-04 adds that, plus a real `rounds_to_success`).

    Round 1 always uses `base_payload` unchanged -- deliberately identical
    to whatever the suite's own static attack machinery would produce
    (S1/S2/S3's existing ASR@1 baseline), a property S4-07's ASR@1-vs-ASR@20
    comparison relies on. Every round is driven through the same override
    hook (`run_episode`'s `injections_override` / `run_mcp_episode`'s
    `injection_text_override`) rather than round 1 taking a different code
    path from round 2+ -- see docs/notes/adaptive_attacks.md's
    "Implementation decisions" section.

    `suite == "mcp"` dispatches to `run_mcp_episode_fn` with a bare payload
    string (mirrors `mcp_adapter.PoisonedCase.injection_text`); any other
    suite dispatches to `run_episode_fn` with `injections_override` built by
    broadcasting the same payload to every entry in `injection_points` --
    the same "one string applied to every injection candidate identically"
    simplification `FixedJailbreakAttack.attack()` already makes.
    `injection_points` must be non-empty for a non-mcp suite (the caller
    computes it once, up front, from a single real `attack.attack()` call --
    see S4-05/S4-06).

    A **fresh** `DefenseStack` is built via `defense_stack_factory` for
    *every* round, never reused across rounds: a stateful defense (e.g.
    `GuardModel`) accumulates its own `.responses` list, and
    `_write_episode_trace`/`_write_mcp_episode_trace` re-inserts every entry
    in that list as a `cost_record` row on every episode it writes -- reusing
    one `DefenseStack` across rounds would double-count (triple-, ...)
    earlier rounds' defense cost into every later round.

    **Known limitation, not deferred work:** unlike `sweep/runner.py`'s
    per-episode resumability, a trial interrupted mid-loop does not resume
    round-by-round on restart -- re-running it starts over from round 1.
    Acceptable at S4-05's cost-capped scale; would need revisiting to scale
    this up.
    """
    if suite == "mcp":
        if run_mcp_episode_fn is None:
            raise ValueError("run_mcp_episode_fn is required for suite 'mcp'")
    else:
        if run_episode_fn is None:
            raise ValueError("run_episode_fn is required for a non-mcp suite")
        if not injection_points:
            raise ValueError("injection_points is required for a non-mcp suite")

    trial_id = insert_adaptive_trial(
        conn,
        run_id=run_id,
        task_id=task_id,
        defense=defense_name,
        attack_family=attack_family,
        suite=suite,
        budget=budget,
        started_at=_now(),
    )

    payload = base_payload
    any_success = False
    rounds_run = 0

    for round_index in range(1, budget + 1):
        defense_stack = defense_stack_factory()

        if suite == "mcp":
            assert run_mcp_episode_fn is not None
            episode_result = run_mcp_episode_fn(
                conn=conn,
                run_id=run_id,
                user_task_id=user_task_id,
                poisoned_case_id=poisoned_case_id,
                model_client=model_client,
                defense_stack=defense_stack,
                defense_name=defense_name,
                model_name=model_name,
                injection_text_override=payload,
            )
        else:
            assert run_episode_fn is not None
            episode_result = run_episode_fn(
                conn=conn,
                run_id=run_id,
                suite_name=suite,
                user_task_id=user_task_id,
                injection_task_id=injection_task_id,
                model_client=model_client,
                defense_stack=defense_stack,
                defense_name=defense_name,
                model_name=model_name,
                attack_name=attack_family,
                injections_override={point: payload for point in injection_points},
            )

        with transaction(conn):
            insert_adaptive_round(
                conn,
                trial_id=trial_id,
                round_index=round_index,
                episode_id=episode_result.episode_id,
                payload_text=payload,
                timestamp=_now(),
            )
        rounds_run = round_index

        feedback = extract_feedback_fn(conn, episode_id=episode_result.episode_id)
        if feedback.success:
            any_success = True

        if round_index < budget:
            mutator_responses: list[ModelResponse] = []
            payload = mutate_payload_fn(
                model_client,
                family=attack_family,
                current_payload=payload,
                goal=goal,
                feedback=feedback,
                round_index=round_index + 1,
                responses=mutator_responses,
            )
            if mutator_responses:
                with transaction(conn):
                    for response in mutator_responses:
                        insert_cost_record(
                            conn,
                            run_id=run_id,
                            episode_id=episode_result.episode_id,
                            model=f"mutator:{attack_family}",
                            tokens_in=response.tokens_in,
                            tokens_out=response.tokens_out,
                            wall_ms=response.wall_ms,
                            usd=response.cost.usd,
                            cache_hit=response.cache_hit,
                            timestamp=_now(),
                        )

    update_adaptive_trial_result(
        conn, trial_id=trial_id, success=any_success, rounds_to_success=None, ended_at=_now()
    )

    return AdaptiveTrialResult(
        trial_id=trial_id,
        rounds_run=rounds_run,
        success=any_success,
        rounds_to_success=None,
    )
