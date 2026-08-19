from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from injection_pareto.trace.queries import reconstruct_episode
from injection_pareto.types import Verdict

# `refusal_text` only ever feeds a mutation prompt (S4-02), not a replay --
# capped so one verbose episode can't blow up the mutator's context.
_REFUSAL_TEXT_CAP = 500


@dataclass
class EpisodeFeedback:
    """The three discrete signals an adaptive attacker (S4-02's mutator) gets
    back after one round: whether the injected task succeeded, whether any
    defense hook intervened, and the agent's final visible text. Deliberately
    black-box/discrete -- see docs/notes/adaptive_attacks.md on how this
    differs from the white-box gradient/logit feedback the paper cited for
    this sprint actually uses."""

    success: bool
    defense_intervened: bool
    refusal_text: str | None


def extract_feedback(conn: sqlite3.Connection, *, episode_id: int) -> EpisodeFeedback:
    """Reads `EpisodeFeedback` back off an already-completed, attacked
    episode's trace. Raises `ValueError` for a benign episode
    (`injection_task_id IS NULL`) -- the adaptive loop only ever calls this
    on attacked episodes, so a benign one here means a caller bug."""
    row = conn.execute(
        "SELECT injection_task_id, security FROM episode WHERE id = ?", (episode_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"no episode with id {episode_id}")
    if row["injection_task_id"] is None:
        raise ValueError(f"episode {episode_id} is benign (injection_task_id IS NULL)")

    trace = reconstruct_episode(conn, episode_id)

    defense_intervened = any(
        event.verdict == Verdict.BLOCK.value
        for step in trace.steps
        for event in step.defense_events
    ) or any(event.verdict == Verdict.BLOCK.value for event in trace.episode_defense_events)

    refusal_text: str | None = None
    for step in trace.steps:
        if step.role == "assistant" and step.content:
            refusal_text = step.content[:_REFUSAL_TEXT_CAP]

    return EpisodeFeedback(
        success=bool(row["security"]),
        defense_intervened=defense_intervened,
        refusal_text=refusal_text,
    )
