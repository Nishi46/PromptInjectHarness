"""S2-12 — auto-generate `results/static_baseline.md` from a sweep's trace
DB. Every number in the output comes from a query against the DB — never
hand-copy a number into the markdown; re-run this script to refresh it.

Usage: .venv/bin/python scripts/generate_static_baseline.py [trace_db_path]
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from injection_pareto.scoring import (
    UtilityRate,
    benign_utility_rate,
    score_episode_partial_compromise,
)
from injection_pareto.trace import connect, cost_summary_by_episode

DEFAULT_TRACE_DB_PATH = Path("runs/local/static_sweep/trace.db")
OUTPUT_PATH = Path("results/static_baseline.md")


def _backfill_partial_compromise(conn: sqlite3.Connection) -> int:
    """Runs S2-08's post-hoc scorer over every attacked episode that hasn't
    been scored yet (the sweep itself doesn't call it inline — see
    `scoring/security.py`). Returns the number backfilled."""
    rows = conn.execute(
        "SELECT id FROM episode WHERE injection_task_id IS NOT NULL AND partial_compromise IS NULL"
    ).fetchall()
    for row in rows:
        score_episode_partial_compromise(conn, episode_id=row["id"])
    return len(rows)


@dataclass
class SecurityRow:
    defense: str
    attack: str
    model: str
    n_episodes: int
    asr: float
    partial_rate: float


def _security_table(conn: sqlite3.Connection) -> list[SecurityRow]:
    """Attack success rate (fraction with `security=1`, i.e. the injection
    actually succeeded -- see docs/reproduction.md's correction note for why
    that's the right polarity) and partial-compromise rate, grouped by
    (defense, attack, model), over attacked episodes only."""
    rows = conn.execute(
        """
        SELECT r.defense_stack AS defense, r.attack AS attack, r.model AS model,
               e.security AS security, e.partial_compromise AS partial_compromise
        FROM episode e JOIN run r ON e.run_id = r.id
        WHERE e.injection_task_id IS NOT NULL
        """
    ).fetchall()

    grouped: dict[tuple[str, str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[(row["defense"], row["attack"], row["model"])].append(row)

    result = []
    for (defense, attack, model), group in sorted(grouped.items()):
        n = len(group)
        asr = sum(1 for r in group if r["security"]) / n
        partial_rate = sum(1 for r in group if r["partial_compromise"]) / n
        result.append(SecurityRow(defense, attack, model, n, asr, partial_rate))
    return result


@dataclass
class CostRow:
    defense: str
    model: str
    n_episodes: int
    total_usd: float
    mean_episode_p95_wall_ms: float


def _cost_table(conn: sqlite3.Connection) -> list[CostRow]:
    """Total $ and typical per-episode tail latency, grouped by (defense,
    model), across every episode (benign and attacked). Built on top of
    `trace/queries.py::cost_summary_by_episode`, which computes each
    episode's own p95 over its calls; this aggregates those episode-level
    p95s (mean across episodes), not a second p95 over p95s."""
    episode_meta = {
        row["id"]: (row["defense_stack"], row["model"])
        for row in conn.execute(
            "SELECT e.id AS id, r.defense_stack, r.model "
            "FROM episode e JOIN run r ON e.run_id = r.id"
        ).fetchall()
    }

    grouped_usd: dict[tuple[str, str], float] = defaultdict(float)
    grouped_p95s: dict[tuple[str, str], list[float]] = defaultdict(list)
    grouped_n: dict[tuple[str, str], int] = defaultdict(int)
    for summary in cost_summary_by_episode(conn):
        key = episode_meta.get(summary.episode_id)
        if key is None:
            continue
        grouped_usd[key] += summary.total_usd
        grouped_n[key] += 1
        if summary.p95_wall_ms is not None:
            grouped_p95s[key].append(summary.p95_wall_ms)

    result = []
    for key in sorted(grouped_n):
        defense, model = key
        p95s = grouped_p95s[key]
        result.append(
            CostRow(
                defense=defense,
                model=model,
                n_episodes=grouped_n[key],
                total_usd=grouped_usd[key],
                mean_episode_p95_wall_ms=statistics.mean(p95s) if p95s else 0.0,
            )
        )
    return result


@dataclass
class GuardScoreSummary:
    n_scores: int
    n_parsed: int
    n_blocked: int
    n_allowed: int
    min_score: float | None
    mean_score: float | None
    median_score: float | None
    max_score: float | None


def _guard_score_summary(conn: sqlite3.Connection) -> GuardScoreSummary:
    """Descriptive stats over every guard-model score recorded in this
    sweep (`detail_json` is double-encoded JSON -- see S2-05's `GuardModel`:
    the adapter always wraps a hook's `reason` as `{"reason": ..., ...}`, so
    the guard's own `{"score": ..., "threshold": ..., "parsed": ...}`
    payload sits one level inside that). A full summary, not just a binary
    block/allow split -- this is exactly the score distribution S6-07's ROC
    curve will need later; this script only summarizes it."""
    scores: list[float] = []
    n_blocked = 0
    n_allowed = 0
    n_parsed = 0
    rows = conn.execute(
        "SELECT verdict, detail_json FROM defense_event "
        "WHERE defense_name = 'guard_model' AND hook = 'on_tool_result' AND detail_json IS NOT NULL"
    ).fetchall()
    for row in rows:
        if row["verdict"] == "block":
            n_blocked += 1
        elif row["verdict"] == "allow":
            n_allowed += 1
        try:
            outer = json.loads(row["detail_json"])
            inner = json.loads(outer["reason"])
            if inner.get("parsed"):
                n_parsed += 1
            scores.append(float(inner["score"]))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue

    return GuardScoreSummary(
        n_scores=len(scores),
        n_parsed=n_parsed,
        n_blocked=n_blocked,
        n_allowed=n_allowed,
        min_score=min(scores) if scores else None,
        mean_score=statistics.mean(scores) if scores else None,
        median_score=statistics.median(scores) if scores else None,
        max_score=max(scores) if scores else None,
    )


def _render_markdown(
    *,
    trace_db_path: Path,
    security_rows: list[SecurityRow],
    utility_rows: list[UtilityRate],
    cost_rows: list[CostRow],
    guard_summary: GuardScoreSummary,
    backfilled: int,
) -> str:
    lines = [
        "# Static Baseline Results (S2-11 / S2-12)",
        "",
        f"Auto-generated by `scripts/generate_static_baseline.py` against "
        f"`{trace_db_path}` on {datetime.now(UTC).isoformat()}. "
        "Every number below comes from a query against the trace DB -- "
        "never hand-edited; re-run the script to refresh after a new sweep.",
        "",
        f"(Backfilled `partial_compromise` for {backfilled} previously-unscored "
        "attacked episode(s) via S2-08's `score_episode_partial_compromise` "
        "before generating this table.)"
        if backfilled
        else "(No episodes needed `partial_compromise` backfilling -- already scored.)",
        "",
        "## Security: attack success rate by (defense, attack, model)",
        "",
        "`security=True` means the injection actually succeeded (see "
        "`docs/reproduction.md`'s correction note); ASR is the fraction of "
        "attacked episodes where that happened. `partial compromise` is "
        "S2-08's steps-toward-goal signal: attacked but not fully compromised, "
        "with a matching ground-truth tool call in the trace.",
        "",
        "| Defense | Attack | Model | Episodes | ASR | Partial compromise |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for sec_row in security_rows:
        lines.append(
            f"| {sec_row.defense} | {sec_row.attack} | {sec_row.model} | {sec_row.n_episodes} "
            f"| {sec_row.asr:.3f} | {sec_row.partial_rate:.3f} |"
        )

    lines += [
        "",
        "## Utility: benign task completion by (defense, model)",
        "",
        "Injection-free episodes only (S2-09's `benign_utility_rate` -- "
        "measuring utility on attacked episodes would invalidate every "
        "number in the project).",
        "",
        "| Defense | Model | Episodes | Utility rate |",
        "| --- | --- | --- | --- |",
    ]
    for util_row in sorted(utility_rows, key=lambda r: (r.defense, r.model)):
        lines.append(
            f"| {util_row.defense} | {util_row.model} | {util_row.n_episodes} "
            f"| {util_row.rate:.3f} |"
        )

    lines += [
        "",
        "## Cost & latency by (defense, model)",
        "",
        "All episodes (benign + attacked). `$` is real spend (list-price "
        "modeled for hosted calls, `$0` for local Ollama). Latency is the "
        "mean, across this group's episodes, of each episode's own p95 "
        "call latency.",
        "",
        "| Defense | Model | Episodes | Total $ | Mean episode p95 (ms) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for cost_row in cost_rows:
        lines.append(
            f"| {cost_row.defense} | {cost_row.model} | {cost_row.n_episodes} "
            f"| {cost_row.total_usd:.6f} | {cost_row.mean_episode_p95_wall_ms:.0f} |"
        )

    lines += [
        "",
        "## Guard model (D4) score distribution",
        "",
        "Summary only (full distribution lives in `defense_event.detail_json` "
        "for S6-07's ROC curve later).",
        "",
        f"- Scores recorded: {guard_summary.n_scores} "
        f"({guard_summary.n_parsed} parsed successfully)",
        f"- Verdicts: {guard_summary.n_blocked} blocked, {guard_summary.n_allowed} allowed",
    ]
    if guard_summary.n_scores:
        lines.append(
            f"- Score range: min={guard_summary.min_score:.4f} "
            f"mean={guard_summary.mean_score:.4f} "
            f"median={guard_summary.median_score:.4f} "
            f"max={guard_summary.max_score:.4f}"
        )
    else:
        lines.append("- No guard-model episodes in this trace DB.")

    return "\n".join(lines) + "\n"


def main() -> None:
    trace_db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TRACE_DB_PATH
    conn = connect(trace_db_path)
    try:
        backfilled = _backfill_partial_compromise(conn)
        security_rows = _security_table(conn)
        utility_rows = benign_utility_rate(conn)
        cost_rows = _cost_table(conn)
        guard_summary = _guard_score_summary(conn)
    finally:
        conn.close()

    markdown = _render_markdown(
        trace_db_path=trace_db_path,
        security_rows=security_rows,
        utility_rows=utility_rows,
        cost_rows=cost_rows,
        guard_summary=guard_summary,
        backfilled=backfilled,
    )
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(markdown)
    print(
        f"wrote {OUTPUT_PATH} "
        f"({len(security_rows)} security rows, {len(utility_rows)} utility rows)"
    )


if __name__ == "__main__":
    main()
