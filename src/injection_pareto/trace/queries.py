from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass


@dataclass
class ToolCallRow:
    id: int
    tool_name: str
    arguments_json: str
    result_json: str | None
    blocked_by_defense: str | None
    timestamp: str


@dataclass
class DefenseEventRow:
    id: int
    defense_name: str
    hook: str
    verdict: str
    detail_json: str | None
    timestamp: str


@dataclass
class StepRow:
    id: int
    step_index: int
    role: str
    content: str
    timestamp: str
    tool_calls: list[ToolCallRow]
    defense_events: list[DefenseEventRow]


@dataclass
class EpisodeTrace:
    episode_id: int
    steps: list[StepRow]
    episode_defense_events: list[DefenseEventRow]


def reconstruct_episode(conn: sqlite3.Connection, episode_id: int) -> EpisodeTrace:
    """Rebuild one episode's full timeline: steps, their tool calls, and defense events."""
    step_rows = conn.execute(
        "SELECT id, step_index, role, content, timestamp FROM step "
        "WHERE episode_id = ? ORDER BY step_index",
        (episode_id,),
    ).fetchall()

    steps: list[StepRow] = []
    for row in step_rows:
        step_id = row["id"]
        tool_calls = [
            ToolCallRow(**dict(tc))
            for tc in conn.execute(
                "SELECT id, tool_name, arguments_json, result_json, blocked_by_defense, timestamp "
                "FROM tool_call WHERE step_id = ? ORDER BY timestamp",
                (step_id,),
            ).fetchall()
        ]
        defense_events = [
            DefenseEventRow(**dict(de))
            for de in conn.execute(
                "SELECT id, defense_name, hook, verdict, detail_json, timestamp "
                "FROM defense_event WHERE step_id = ? ORDER BY timestamp",
                (step_id,),
            ).fetchall()
        ]
        steps.append(
            StepRow(
                id=step_id,
                step_index=row["step_index"],
                role=row["role"],
                content=row["content"],
                timestamp=row["timestamp"],
                tool_calls=tool_calls,
                defense_events=defense_events,
            )
        )

    episode_defense_events = [
        DefenseEventRow(**dict(de))
        for de in conn.execute(
            "SELECT id, defense_name, hook, verdict, detail_json, timestamp "
            "FROM defense_event WHERE episode_id = ? AND step_id IS NULL ORDER BY timestamp",
            (episode_id,),
        ).fetchall()
    ]

    return EpisodeTrace(
        episode_id=episode_id, steps=steps, episode_defense_events=episode_defense_events
    )


@dataclass
class EpisodeCostSummary:
    episode_id: int
    total_usd: float
    p95_wall_ms: float | None


def _percentile(sorted_values: list[int], p: float) -> float | None:
    """Linear-interpolation percentile. SQLite has no native percentile
    aggregate, so this is computed in Python over rows pulled by one query."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (len(sorted_values) - 1) * p
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return float(sorted_values[int(rank)])
    lower_weight = sorted_values[lower] * (upper - rank)
    upper_weight = sorted_values[upper] * (rank - lower)
    return lower_weight + upper_weight


def cost_summary_by_episode(
    conn: sqlite3.Connection, *, run_id: int | None = None
) -> list[EpisodeCostSummary]:
    """Total $ and p95 wall-clock latency per episode, optionally scoped to one run."""
    query = "SELECT episode_id, usd, wall_ms FROM cost_record"
    params: tuple[int, ...] = ()
    if run_id is not None:
        query += " WHERE run_id = ?"
        params = (run_id,)
    rows = conn.execute(query, params).fetchall()

    by_episode: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        by_episode.setdefault(row["episode_id"], []).append(row)

    summaries = []
    for episode_id, episode_rows in by_episode.items():
        total_usd = sum(r["usd"] for r in episode_rows)
        wall_ms_values = sorted(r["wall_ms"] for r in episode_rows)
        summaries.append(
            EpisodeCostSummary(
                episode_id=episode_id,
                total_usd=total_usd,
                p95_wall_ms=_percentile(wall_ms_values, 0.95),
            )
        )
    return summaries
