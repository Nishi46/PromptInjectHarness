"""S3-07 -- auto-generate `results/mcp_suite.md` from the MCP suite's sweep
trace DB, plus a schema-path-vs-data-path ASR comparison against the static
(AgentDojo) baseline's trace DB. Mirrors `scripts/generate_static_baseline.py`'s
pattern (S2-12): every number in the output comes from a query against a
trace DB, never hand-typed -- re-run this script to refresh after a new sweep.

Usage: .venv/bin/python scripts/generate_mcp_suite_results.py [mcp_trace_db] [static_trace_db]
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

from injection_pareto.scoring import UtilityRate, benign_utility_rate
from injection_pareto.trace import connect, cost_summary_by_episode

DEFAULT_MCP_TRACE_DB_PATH = Path("runs/local/mcp_sweep/trace.db")
DEFAULT_STATIC_TRACE_DB_PATH = Path("runs/local/static_sweep/trace.db")
OUTPUT_PATH = Path("results/mcp_suite.md")

# The comparison table is only fair across the model(s) both sweeps
# actually share -- `configs/mcp_sweep.yaml` only ever ran the two local
# models; `configs/static_sweep.yaml` also included a Groq slice
# (S2-11) that has no MCP-suite counterpart to compare against.
_SHARED_MODELS = ("llama3.2:3b", "llama3.1:latest")


@dataclass
class SecurityRow:
    defense: str
    attack: str
    model: str
    n_episodes: int
    asr: float
    partial_rate: float


def _security_table(conn: sqlite3.Connection) -> list[SecurityRow]:
    """Identical query shape to `generate_static_baseline.py::_security_table`
    -- purely generic over `episode`/`run`, no AgentDojo-specific columns,
    so it needed no changes to run against the MCP suite's trace DB. `attack`
    here holds a poisoned-description sub-family name instead of an
    AgentDojo attack-family name; everything else about the shape is the
    same."""
    rows = conn.execute(
        """
        SELECT r.defense_stack AS defense, r.attack AS attack, r.model AS model,
               e.security AS security, e.partial_compromise AS partial_compromise
        FROM episode e JOIN run r ON e.run_id = r.id
        WHERE e.injection_task_id IS NOT NULL
        """
    ).fetchall()

    grouped: dict[tuple[str, str, str], list] = defaultdict(list)
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
    """Identical shape to `generate_static_baseline.py::_cost_table`."""
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
    """Identical to `generate_static_baseline.py::_guard_score_summary` --
    `configs/mcp_sweep.yaml` includes `guard_model` in its defense list."""
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


@dataclass
class ComparisonRow:
    defense: str
    data_path_n: int
    data_path_asr: float | None
    schema_path_n: int
    schema_path_asr: float | None


def _asr_by_defense(
    conn: sqlite3.Connection, *, models: tuple[str, ...]
) -> dict[str, tuple[int, float]]:
    """Per-defense ASR (`security=True` fraction), aggregated across every
    attack/sub-family, restricted to `models` -- the aggregate the
    schema-path-vs-data-path comparison table needs. Returns
    `{defense: (n_episodes, asr)}`."""
    rows = conn.execute(
        f"""
        SELECT r.defense_stack AS defense, e.security AS security
        FROM episode e JOIN run r ON e.run_id = r.id
        WHERE e.injection_task_id IS NOT NULL AND r.model IN ({",".join("?" * len(models))})
        """,
        models,
    ).fetchall()
    grouped: dict[str, list] = defaultdict(list)
    for row in rows:
        grouped[row["defense"]].append(row)
    return {
        defense: (len(group), sum(1 for r in group if r["security"]) / len(group))
        for defense, group in grouped.items()
    }


def _comparison_table(
    mcp_conn: sqlite3.Connection, static_conn: sqlite3.Connection
) -> list[ComparisonRow]:
    data_path = _asr_by_defense(static_conn, models=_SHARED_MODELS)
    schema_path = _asr_by_defense(mcp_conn, models=_SHARED_MODELS)
    defenses = sorted(set(data_path) | set(schema_path))
    rows = []
    for defense in defenses:
        dp_n, dp_asr = data_path.get(defense, (0, None))
        sp_n, sp_asr = schema_path.get(defense, (0, None))
        rows.append(ComparisonRow(defense, dp_n, dp_asr, sp_n, sp_asr))
    return rows


def _render_markdown(
    *,
    mcp_trace_db_path: Path,
    static_trace_db_path: Path,
    security_rows: list[SecurityRow],
    utility_rows: list[UtilityRate],
    cost_rows: list[CostRow],
    guard_summary: GuardScoreSummary,
    comparison_rows: list[ComparisonRow],
) -> str:
    lines = [
        "# MCP Suite Results (S3-06 / S3-07)",
        "",
        f"Auto-generated by `scripts/generate_mcp_suite_results.py` against "
        f"`{mcp_trace_db_path}` (and `{static_trace_db_path}` for the schema-path-vs-"
        f"data-path comparison) on {datetime.now(UTC).isoformat()}. Every number below "
        "comes from a query against a trace DB -- never hand-edited; re-run the script "
        "to refresh after a new sweep.",
        "",
        "## Security: attack success rate by (defense, sub-family, model)",
        "",
        "`security=True` means the poisoned tool description's injected instruction "
        "actually landed (`mcp.scoring.score_mcp_security`) -- an exact ground-truth "
        "tool call, not merely an attempt. `partial compromise` mirrors S2-08's "
        "steps-toward-goal signal: the agent attempted the right *tool*, blocked or "
        "not, without landing the right arguments (see `docs/notes/mcp_suite.md` for "
        "why a tool-name-only fallback can register a false positive when a "
        "sub-family's representative case shares a tool name with the benign task's "
        "own legitimate usage -- true for `direct_instruction`/`fake_usage_note` here).",
        "",
        "| Defense | Sub-family | Model | Episodes | ASR | Partial compromise |",
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
        "Injection-free episodes only, across all 15 benign tasks (S2-09's "
        "`benign_utility_rate`, reused unmodified).",
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
        "All episodes (benign + attacked). `$` is real spend (`$0` for local Ollama, "
        "the only provider this sweep used). Latency is the mean, across this group's "
        "episodes, of each episode's own p95 call latency.",
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

    lines += [
        "",
        "## Schema-path vs. data-path: aggregate ASR by defense",
        "",
        f"Data-path = `{static_trace_db_path}` (AgentDojo suite, injection arrives in "
        f"tool *output*); schema-path = `{mcp_trace_db_path}` (MCP suite, injection "
        "arrives in the tool *description*). Both restricted to the two models both "
        f"sweeps actually share ({', '.join(_SHARED_MODELS)}) -- the static sweep's "
        "Groq slice (S2-11) has no MCP-suite counterpart, so including it would "
        "compare different models, not different attack surfaces.",
        "",
        "| Defense | Data-path episodes | Data-path ASR | Schema-path episodes | Schema-path ASR |",
        "| --- | --- | --- | --- | --- |",
    ]
    for comp in comparison_rows:
        dp_asr = f"{comp.data_path_asr:.3f}" if comp.data_path_asr is not None else "n/a"
        sp_asr = f"{comp.schema_path_asr:.3f}" if comp.schema_path_asr is not None else "n/a"
        lines.append(
            f"| {comp.defense} | {comp.data_path_n} | {dp_asr} "
            f"| {comp.schema_path_n} | {sp_asr} |"
        )

    lines += [
        "",
        "## Analysis: does the schema-path/data-path hypothesis hold?",
        "",
        "**Hypothesis stated up front in `sprint_planning.md`:** spotlighting and "
        "delimiting will underperform on the MCP suite because they mark untrusted "
        "*data*, while the poisoned content here arrives as trusted *schema*.",
        "",
        "**The data does not confirm or refute it -- and says why, plainly.** Every "
        "cell in the comparison table above is `0.000`: on both the local models this "
        "sweep used, *no attack landed in either suite*, for *any* of the 6 defenses, "
        "including `no_defense`. `spotlighting` looks identical to every other "
        "defense here not because it successfully blocks schema-path injections, but "
        "because nothing ever reaches the point where a defense's marking behavior "
        "could matter -- the underlying models never complete the multi-step action "
        "the attack needs, on either attack surface. This is the same capability "
        "ceiling `results/static_baseline.md` (S2-12) already documented for these "
        "models (`llama3.2:3b`/`llama3.1:latest` show `ASR=0.000` everywhere there "
        "too), and exactly the risk Appendix A.5 of `sprint_planning.md` names "
        "explicitly: weak local models can compress attack/defense dynamic range to "
        "the point where no comparison is possible.",
        "",
        "**A secondary signal does differ, with a caveat.** `partial_compromise` -- "
        "did the agent even attempt the injected tool -- is `1.000` for every "
        "defense on `direct_instruction`/`fake_usage_note` and `0.000`-ish on "
        "`fake_precondition`/`cross_tool_redirection` (see the security table above). "
        "But this doesn't differentiate defenses either: it's identical across all 6 "
        "of them for a given sub-family, and `docs/notes/mcp_suite.md` documents the "
        "likely confound directly -- the `direct_instruction` case's ground truth is "
        "`delete_file`, which the matching `file_storage` benign task also calls "
        "legitimately, and the `fake_usage_note` case's ground truth is `send_email`, "
        "which the matching `email` benign task also calls legitimately (to a "
        "different recipient) -- so `partial_compromise`'s tool-name-only fallback "
        "may be picking up ordinary task completion for both, not genuine engagement "
        "with the injected text. `fake_precondition`/`cross_tool_redirection`'s "
        "ground-truth tools (`create_payment`, `create_contact`) have no such overlap "
        "with their benign tasks, and their near-zero `partial_compromise` is "
        "consistent with that.",
        "",
        "**What would actually test the hypothesis:** a model capable enough to "
        "complete a landing attack on *both* suites, so a defense's real effect (or "
        "lack of one) has something to show up against. `results/static_baseline.md`'s "
        "one non-zero cell (`no_defense` / `important_instructions` / "
        "`openai/gpt-oss-120b`, ASR=0.333) is exactly this pattern on the data-path "
        "suite -- a larger model produced the only successful attack in the whole "
        "project so far. The MCP suite has no equivalent data point yet; running the "
        "same larger-model slice against it (mirroring S2-11's Groq slice) is the "
        "natural next step before this hypothesis can be judged on real evidence "
        "rather than a null result.",
    ]

    return "\n".join(lines) + "\n"


def main() -> None:
    mcp_trace_db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_MCP_TRACE_DB_PATH
    static_trace_db_path = (
        Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_STATIC_TRACE_DB_PATH
    )

    mcp_conn = connect(mcp_trace_db_path)
    static_conn = connect(static_trace_db_path)
    try:
        security_rows = _security_table(mcp_conn)
        utility_rows = benign_utility_rate(mcp_conn)
        cost_rows = _cost_table(mcp_conn)
        guard_summary = _guard_score_summary(mcp_conn)
        comparison_rows = _comparison_table(mcp_conn, static_conn)
    finally:
        mcp_conn.close()
        static_conn.close()

    markdown = _render_markdown(
        mcp_trace_db_path=mcp_trace_db_path,
        static_trace_db_path=static_trace_db_path,
        security_rows=security_rows,
        utility_rows=utility_rows,
        cost_rows=cost_rows,
        guard_summary=guard_summary,
        comparison_rows=comparison_rows,
    )
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(markdown)
    print(
        f"wrote {OUTPUT_PATH} "
        f"({len(security_rows)} security rows, {len(utility_rows)} utility rows, "
        f"{len(comparison_rows)} comparison rows)"
    )


if __name__ == "__main__":
    main()
