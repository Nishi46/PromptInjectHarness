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
    signal (S4-01). Stops at the first successful round (S4-04) -- a round
    that succeeds never gets mutated further, and no later round runs.
    `AdaptiveTrialResult.rounds_to_success` is the 1-indexed round number of
    that first success, or `None` if the trial exhausted `budget` rounds
    without one -- a richer signal than the binary `success` flag alone: a
    defense broken on round 2 is meaningfully weaker than one broken on
    round 19, even though both count as `success=True` under ASR@20.

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

    A round whose episode *fails to execute at all* (an exception from
    `run_episode_fn`/`run_mcp_episode_fn`) is treated as a failed round, not
    a trial-ending error -- found for real running S4-05's first full
    sweep: `run_episode`'s AgentDojo suite builds its environment via
    `raw_yaml_text.format(**injections)` then `yaml.safe_load()`s the
    result, so a free-form LLM-mutated payload containing an unescaped
    quote/colon can raise a `yaml.YAMLError` before the agent ever runs.
    That round writes no `adaptive_round` row (no episode was created to
    link one to) but still counts toward `budget` and still feeds a
    synthetic failure `EpisodeFeedback` into the next mutation, so the loop
    self-corrects instead of a single bad mutation ending the whole trial.
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
    rounds_to_success: int | None = None
    last_episode_id: int | None = None

    for round_index in range(1, budget + 1):
        defense_stack = defense_stack_factory()

        try:
            if suite == "mcp":
                assert run_mcp_episode_fn is not None
                episode_result: _EpisodeResultLike | None = run_mcp_episode_fn(
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
        except Exception as exc:
            # A mutated payload can break the *harness* itself, not just fail
            # to compromise the target -- observed for real in S4-05: an
            # AgentDojo suite builds its environment via
            # `raw_yaml_text.format(**injections)` then `yaml.safe_load()`s
            # the result (`TaskSuite.load_and_inject_default_environment`),
            # so a free-form LLM-authored payload containing an unescaped
            # quote/colon can produce a `yaml.YAMLError` well before the
            # agent ever runs. From the attacker's perspective this is
            # exactly as much a failed round as one a defense blocks -- it
            # must feed back into the next mutation, not abort the whole
            # trial. No episode was created, so there's no `adaptive_round`
            # row to write for this round (the FK requires a real episode).
            episode_result = None
            feedback = EpisodeFeedback(
                success=False,
                defense_intervened=False,
                refusal_text=f"round {round_index} failed to execute: {exc}"[:500],
            )
        else:
            assert episode_result is not None
            with transaction(conn):
                insert_adaptive_round(
                    conn,
                    trial_id=trial_id,
                    round_index=round_index,
                    episode_id=episode_result.episode_id,
                    payload_text=payload,
                    timestamp=_now(),
                )
            last_episode_id = episode_result.episode_id
            feedback = extract_feedback_fn(conn, episode_id=episode_result.episode_id)

        rounds_run = round_index

        if feedback.success:
            any_success = True
            rounds_to_success = round_index
            break

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
            # Attributed to the most recent real episode, if any -- a round
            # whose own episode failed to execute (see above) has none of
            # its own to attribute this mutation's cost to.
            if mutator_responses and last_episode_id is not None:
                with transaction(conn):
                    for response in mutator_responses:
                        insert_cost_record(
                            conn,
                            run_id=run_id,
                            episode_id=last_episode_id,
                            model=f"mutator:{attack_family}",
                            tokens_in=response.tokens_in,
                            tokens_out=response.tokens_out,
                            wall_ms=response.wall_ms,
                            usd=response.cost.usd,
                            cache_hit=response.cache_hit,
                            timestamp=_now(),
                        )

    update_adaptive_trial_result(
        conn,
        trial_id=trial_id,
        success=any_success,
        rounds_to_success=rounds_to_success,
        ended_at=_now(),
    )

    return AdaptiveTrialResult(
        trial_id=trial_id,
        rounds_run=rounds_run,
        success=any_success,
        rounds_to_success=rounds_to_success,
    )
