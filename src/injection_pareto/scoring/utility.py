from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass


class InjectionFreeViolation(AssertionError):
    """Raised when utility is about to be computed over an episode set that
    includes at least one attacked episode (`injection_task_id IS NOT NULL`).

    Measuring utility on attacked episodes silently invalidates every number
    in the project (sprint_planning.md's Sprint 2 "Watch for" note and risk
    register both call this out explicitly) -- this must be a hard failure,
    never a warning or a silent skip.
    """


@dataclass
class UtilityRow:
    episode_id: int
    injection_task_id: str | None
    utility: bool | None
    defense: str
    model: str


def assert_injection_free(rows: Sequence[UtilityRow]) -> None:
    """The critical assertion itself, exposed standalone so it's directly
    unit-testable against a fixture -- independent of `benign_utility_rate`'s
    SQL query, which normally makes violating this impossible in practice by
    filtering to `injection_task_id IS NULL` before rows ever reach here.
    `benign_utility_rate` still calls this as defense-in-depth: if that
    query's `WHERE` clause ever regresses (e.g. a future edit drops it by
    mistake), this catches it immediately instead of silently returning a
    wrong rate."""
    attacked = [row.episode_id for row in rows if row.injection_task_id is not None]
    if attacked:
        raise InjectionFreeViolation(
            "utility must only ever be computed on injection-free episodes; "
            f"found {len(attacked)} attacked episode(s): {attacked}"
        )


@dataclass
class UtilityRate:
    defense: str
    model: str
    n_episodes: int
    n_success: int
    rate: float


def benign_utility_rate(
    conn: sqlite3.Connection, *, run_id: int | None = None
) -> list[UtilityRate]:
    """Benign task completion rate per (defense, model) pair -- the
    injection-free half of "security and utility both measured for all 6
    defenses on the same task set" (Sprint 2 acceptance criterion). Defense
    and model come from the `run` table (a `run` can mix benign and attacked
    episodes -- see S1-08's reproduction run -- so this filters at the row
    level, not by excluding whole runs).

    Episodes with `utility IS NULL` (not yet scored) are excluded from both
    the numerator and denominator, not counted as failures.
    """
    query = (
        "SELECT e.id AS episode_id, e.injection_task_id, e.utility, "
        "r.defense_stack AS defense, r.model AS model "
        "FROM episode e JOIN run r ON e.run_id = r.id "
        "WHERE e.injection_task_id IS NULL"
    )
    params: tuple[int, ...] = ()
    if run_id is not None:
        query += " AND e.run_id = ?"
        params = (run_id,)

    rows = [
        UtilityRow(
            episode_id=r["episode_id"],
            injection_task_id=r["injection_task_id"],
            utility=None if r["utility"] is None else bool(r["utility"]),
            defense=r["defense"],
            model=r["model"],
        )
        for r in conn.execute(query, params).fetchall()
    ]
    assert_injection_free(rows)

    grouped: dict[tuple[str, str], list[bool]] = {}
    for row in rows:
        if row.utility is None:
            continue
        grouped.setdefault((row.defense, row.model), []).append(row.utility)

    return [
        UtilityRate(
            defense=defense,
            model=model,
            n_episodes=len(values),
            n_success=sum(values),
            rate=sum(values) / len(values),
        )
        for (defense, model), values in grouped.items()
    ]
