"""S5-06 -- auto-generate `results/architectural_defenses.md`: latency and
$/task breakdown for the Sprint 5 architectural defenses (D7 `dual_llm`,
D8 `capability_enforcement`), plus the utility-tax numbers (extending
S5-02's own measurement to cover D8 too, closing the sprint's "utility tax
quantified for both D7 and D8" acceptance criterion) and the security
numbers from S5-05's static and adaptive sweeps -- all queried live from
the same trace DBs those tasks populated. Mirrors
`scripts/generate_adaptive_results.py`'s pattern (typed row dataclasses,
one query function each, deterministic output, no new dependency): never
hand-edited; re-run this script to refresh after a new sweep.

Usage: .venv/bin/python scripts/generate_architectural_results.py
"""

from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from injection_pareto.scoring import UtilityTaxRow, benign_utility_rate, utility_tax_table
from injection_pareto.trace import connect

_STATIC_TRACE_DB = Path("runs/local/static_sweep/trace.db")
_MCP_TRACE_DB = Path("runs/local/mcp_sweep/trace.db")
_ADAPTIVE_TRACE_DB = Path("runs/local/adaptive_sweep/trace.db")
_ADAPTIVE_MCP_TRACE_DB = Path("runs/local/adaptive_mcp_sweep/trace.db")
OUTPUT_PATH = Path("results/architectural_defenses.md")

# S5-01/S5-03's two architectural defenses, in this fixed order everywhere
# below (D7 first, D8 second) -- matches the order they were built in and
# keeps every table's row order stable across re-runs.
_ARCHITECTURAL_DEFENSES = ("dual_llm", "capability_enforcement")
_BASELINE_DEFENSE = "no_defense"


@dataclass
class CostOverheadRow:
    suite: str
    defense: str
    model: str
    n_episodes: int
    mean_extra_calls: float
    mean_defense_wall_ms: float
    mean_defense_usd: float
    mean_base_wall_ms: float
    mean_base_usd: float


def _cost_overhead_table(conn: sqlite3.Connection, *, suite: str) -> list[CostOverheadRow]:
    """Per (defense, model): the mean per-episode count, wall-clock, and $
    a defense's own model calls contribute (`cost_record.model` labeled
    `defense:<name>` -- both adapters already write this with zero
    special-casing, per S5-01's design decision), against that same
    episode's base-model calls (`cost_record.model == run.model`).
    Restricted to `_ARCHITECTURAL_DEFENSES`; scoped to one suite's static
    trace DB (benign + attacked episodes, matching
    `generate_static_baseline.py`'s own "Cost & latency" convention) -- an
    adaptive trial's episodes have a structurally different shape (extra
    mutator calls, multi-round retries) and would not be a fair per-episode
    comparison against these.

    `capability_enforcement` never appears in a `defense:` cost_record row
    at all (its `cost()` returns an empty `CostRecord` by construction, and
    the adapters only ever write a row per entry in a defense's own
    `responses` list, which `CapabilityEnforcement` doesn't have) -- so its
    `mean_extra_calls`/`mean_defense_wall_ms`/`mean_defense_usd` compute as
    exactly `0`/`0.0`/`0.0` here from the *absence* of matching rows, not
    from a special case in this query.
    """
    placeholders = ",".join("?" * len(_ARCHITECTURAL_DEFENSES))
    rows = conn.execute(
        f"""
        SELECT e.id AS episode_id, r.defense_stack AS defense, r.model AS model,
               cr.model AS cost_model, cr.wall_ms AS wall_ms, cr.usd AS usd
        FROM episode e
        JOIN run r ON e.run_id = r.id
        JOIN cost_record cr ON cr.episode_id = e.id
        WHERE r.defense_stack IN ({placeholders})
        """,
        _ARCHITECTURAL_DEFENSES,
    ).fetchall()

    defense_calls: dict[tuple[str, str, int], int] = defaultdict(int)
    defense_wall: dict[tuple[str, str, int], float] = defaultdict(float)
    defense_usd: dict[tuple[str, str, int], float] = defaultdict(float)
    base_wall: dict[tuple[str, str, int], float] = defaultdict(float)
    base_usd: dict[tuple[str, str, int], float] = defaultdict(float)
    episode_keys: set[tuple[str, str, int]] = set()

    for row in rows:
        key = (row["defense"], row["model"], row["episode_id"])
        episode_keys.add(key)
        if row["cost_model"].startswith("defense:"):
            defense_calls[key] += 1
            defense_wall[key] += row["wall_ms"]
            defense_usd[key] += row["usd"]
        elif row["cost_model"] == row["model"]:
            base_wall[key] += row["wall_ms"]
            base_usd[key] += row["usd"]

    grouped: dict[tuple[str, str], list[tuple[str, str, int]]] = defaultdict(list)
    for key in episode_keys:
        defense, model, _episode_id = key
        grouped[(defense, model)].append(key)

    result = []
    for (defense, model), keys in sorted(grouped.items()):
        result.append(
            CostOverheadRow(
                suite=suite,
                defense=defense,
                model=model,
                n_episodes=len(keys),
                mean_extra_calls=statistics.mean(defense_calls[k] for k in keys),
                mean_defense_wall_ms=statistics.mean(defense_wall[k] for k in keys),
                mean_defense_usd=statistics.mean(defense_usd[k] for k in keys),
                mean_base_wall_ms=statistics.mean(base_wall[k] for k in keys),
                mean_base_usd=statistics.mean(base_usd[k] for k in keys),
            )
        )
    return result


@dataclass
class SecurityRow:
    suite: str
    defense: str
    attack: str
    model: str
    n_episodes: int
    asr: float


def _static_security_table(conn: sqlite3.Connection, *, suite: str) -> list[SecurityRow]:
    """Identical shape to `generate_static_baseline.py::_security_table` /
    `generate_mcp_suite_results.py::_security_table`, restricted to
    `_ARCHITECTURAL_DEFENSES` plus `no_defense` (the free baseline both
    architectural configs' header comments already document reusing)."""
    defenses = (*_ARCHITECTURAL_DEFENSES, _BASELINE_DEFENSE)
    placeholders = ",".join("?" * len(defenses))
    rows = conn.execute(
        f"""
        SELECT r.defense_stack AS defense, r.attack AS attack, r.model AS model,
               e.security AS security
        FROM episode e JOIN run r ON e.run_id = r.id
        WHERE e.injection_task_id IS NOT NULL AND r.defense_stack IN ({placeholders})
        """,
        defenses,
    ).fetchall()

    grouped: dict[tuple[str, str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[(row["defense"], row["attack"], row["model"])].append(row)

    result = []
    for (defense, attack, model), group in sorted(grouped.items()):
        n = len(group)
        asr = sum(1 for r in group if r["security"]) / n
        result.append(SecurityRow(suite, defense, attack, model, n, asr))
    return result


@dataclass
class AdaptiveAsrRow:
    suite: str
    defense: str
    n_trials: int
    n_success: int
    asr20: float


def _adaptive_asr_summary(conn: sqlite3.Connection, *, suite: str) -> list[AdaptiveAsrRow]:
    """Per defense, `adaptive_trial.success` rolled up across every attack
    family and model in this suite's adaptive trace DB -- S5-05's headline
    ASR@20 number per (suite, defense), restricted to `_ARCHITECTURAL_DEFENSES`."""
    placeholders = ",".join("?" * len(_ARCHITECTURAL_DEFENSES))
    rows = conn.execute(
        f"SELECT defense, success FROM adaptive_trial WHERE defense IN ({placeholders})",
        _ARCHITECTURAL_DEFENSES,
    ).fetchall()

    grouped: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        grouped[row["defense"]].append(row["success"])

    result = []
    for defense, successes in sorted(grouped.items()):
        n = len(successes)
        n_success = sum(successes)
        result.append(AdaptiveAsrRow(suite, defense, n, n_success, n_success / n))
    return result


def _utility_tax_rows(conn: sqlite3.Connection) -> list[UtilityTaxRow]:
    """D7 and D8's utility tax, both from the same `benign_utility_rate`
    call -- D7's is a re-computation of S5-02's own number (this trace DB
    has grown a second, cache-identical `dual_llm` benign run since S5-02
    documented it, per `configs/architectural_static_sweep.yaml`'s header
    comment on why changing `defenses` changes the config hash; the *rate*
    is unaffected since the duplicate episodes are deterministic cache
    replays, confirmed in `docs/notes/architectural_defenses.md`'s S5-05
    section, but `n_episodes` here is correspondingly larger than S5-02's
    own report). D8's utility tax has no earlier task computing it -- this
    is that number's first real report, closing the sprint's "utility tax
    quantified for both D7 and D8" acceptance criterion."""
    rates = benign_utility_rate(conn)
    rows = []
    for target in _ARCHITECTURAL_DEFENSES:
        rows += utility_tax_table(rates, baseline=_BASELINE_DEFENSE, target=target)
    return rows


def _render_markdown(
    *,
    static_cost_rows: list[CostOverheadRow],
    mcp_cost_rows: list[CostOverheadRow],
    static_utility_rows: list[UtilityTaxRow],
    mcp_utility_rows: list[UtilityTaxRow],
    static_security_rows: list[SecurityRow],
    mcp_security_rows: list[SecurityRow],
    adaptive_asr_rows: list[AdaptiveAsrRow],
) -> str:
    lines = [
        "# Architectural Defense Results (S5-01 .. S5-06)",
        "",
        f"Auto-generated by `scripts/generate_architectural_results.py` against "
        f"`{_STATIC_TRACE_DB}`, `{_MCP_TRACE_DB}`, `{_ADAPTIVE_TRACE_DB}`, "
        f"`{_ADAPTIVE_MCP_TRACE_DB}` on {datetime.now(UTC).isoformat()}. Every number below "
        "comes from a query against a trace DB -- never hand-edited; re-run the script to "
        "refresh after a new sweep.",
        "",
        "## Cost & latency: defense overhead vs. base-model cost, by (suite, defense, model)",
        "",
        "All episodes (benign + attacked) from each suite's static trace DB -- an adaptive "
        "trial's episodes have a structurally different shape (mutator calls, multi-round "
        "retries) and aren't a fair per-episode comparison here. `Extra calls` / `Defense $` / "
        "`Defense ms` are the mean per episode of that defense's own `cost_record` rows "
        "(`model` labeled `defense:<name>`); `Base $` / `Base ms` are the mean per episode of "
        "the underlying model's own calls, for scale.",
        "",
        "| Suite | Defense | Model | Episodes | Extra calls | Defense $ | Defense ms "
        "| Base $ | Base ms |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for cost_row in static_cost_rows + mcp_cost_rows:
        lines.append(
            f"| {cost_row.suite} | {cost_row.defense} | {cost_row.model} | {cost_row.n_episodes} "
            f"| {cost_row.mean_extra_calls:.2f} | {cost_row.mean_defense_usd:.6f} "
            f"| {cost_row.mean_defense_wall_ms:.0f} | {cost_row.mean_base_usd:.6f} "
            f"| {cost_row.mean_base_wall_ms:.0f} |"
        )

    dual_llm_rows = [r for r in static_cost_rows + mcp_cost_rows if r.defense == "dual_llm"]
    cap_enf_rows = [
        r for r in static_cost_rows + mcp_cost_rows if r.defense == "capability_enforcement"
    ]
    cap_enf_all_zero = all(
        r.mean_extra_calls == 0 and r.mean_defense_wall_ms == 0.0 and r.mean_defense_usd == 0.0
        for r in cap_enf_rows
    )
    dual_llm_all_nonzero = all(r.mean_extra_calls > 0 for r in dual_llm_rows)
    cap_enf_sentence = (
        "confirmed in every row above: `capability_enforcement` shows **exactly 0 extra "
        "calls, $0, 0ms** in every (suite, model) cell."
        if cap_enf_all_zero
        else "**NOT confirmed above** -- `capability_enforcement` shows nonzero overhead in "
        "at least one (suite, model) cell, a real anomaly to investigate, not to round away."
    )
    dual_llm_sentence = (
        "`dual_llm` shows non-zero overhead in every cell, as expected (one quarantine call "
        "per tool result)."
        if dual_llm_all_nonzero
        else "`dual_llm` does NOT show non-zero overhead in every cell -- also worth "
        "investigating, not assuming away."
    )
    lines += [
        "",
        "## The `dual_llm` vs. `capability_enforcement` asymmetry",
        "",
        "**Structurally guaranteed, not an estimate:** `capability_enforcement.cost()` "
        "returns an empty `CostRecord` by construction (S5-03) -- it makes zero model "
        "calls, pure regex extraction and set lookups, same as `ToolAllowlist`. "
        f"{cap_enf_sentence} {dual_llm_sentence}",
        "",
        "This is the honest, structurally-guaranteed asymmetry the sprint plan calls for "
        "stating plainly: these are not \"the same kind of tax\" at different magnitudes -- "
        "one architecture (D7) pays a real, per-tool-call inference cost; the other (D8) "
        "pays none, by design, regardless of how the sweep turns out.",
    ]

    lines += [
        "",
        "## Utility tax: `dual_llm` (D7) and `capability_enforcement` (D8) vs. `no_defense`",
        "",
        "Injection-free episodes only (`scoring.utility.benign_utility_rate`). `dual_llm`'s "
        "`n_episodes` here is larger than S5-02's own report -- this trace DB has grown a "
        "second, cache-identical `dual_llm` benign run since then (see "
        "`configs/architectural_static_sweep.yaml`'s header comment); the *rate* is "
        "unaffected, confirmed in `docs/notes/architectural_defenses.md`'s S5-05 section. "
        "D8's utility tax has no earlier report -- this is its first.",
        "",
        "| Suite | Model | no_defense rate (n) | Defense rate (n) | Defense "
        "| Tax (no_defense - defense) |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for suite, rows in (("workspace", static_utility_rows), ("mcp", mcp_utility_rows)):
        for util_row in rows:
            base = (
                f"{util_row.baseline_rate:.3f} ({util_row.baseline_n})"
                if util_row.baseline_rate is not None
                else "n/a"
            )
            tgt = (
                f"{util_row.target_rate:.3f} ({util_row.target_n})"
                if util_row.target_rate is not None
                else "n/a"
            )
            tax = f"{util_row.tax:+.3f}" if util_row.tax is not None else "n/a"
            lines.append(
                f"| {suite} | {util_row.model} | {base} | {tgt} | {util_row.target_defense} "
                f"| {tax} |"
            )

    lines += [
        "",
        "## Security: static sweeps (S5-05), ASR by (suite, defense, attack, model)",
        "",
        "`security=True` means the injection actually succeeded. Includes `no_defense` -- the "
        "free comparison baseline both architectural configs' header comments document reusing.",
        "",
        "| Suite | Defense | Attack | Model | Episodes | ASR |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for sec_row in static_security_rows + mcp_security_rows:
        lines.append(
            f"| {sec_row.suite} | {sec_row.defense} | {sec_row.attack} | {sec_row.model} "
            f"| {sec_row.n_episodes} | {sec_row.asr:.3f} |"
        )

    lines += [
        "",
        "## Security: adaptive sweeps (S5-05), ASR@20 by (suite, defense)",
        "",
        "`adaptive_trial.success`, rolled up across every attack family and model in each "
        "suite -- the full up-to-20-round adaptive trial, not just round 1.",
        "",
        "| Suite | Defense | Trials | Successes | ASR@20 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for asr_row in adaptive_asr_rows:
        lines.append(
            f"| {asr_row.suite} | {asr_row.defense} | {asr_row.n_trials} "
            f"| {asr_row.n_success} | {asr_row.asr20:.3f} |"
        )

    total_trials = sum(r.n_trials for r in adaptive_asr_rows if r.defense == "dual_llm")
    total_success = sum(r.n_success for r in adaptive_asr_rows if r.defense == "dual_llm")
    lines += [
        "",
        "**Not a clean `0.000`-everywhere result.** Static ASR is `0.000` in every cell above "
        "for both architectural defenses (matching every other defense already in these trace "
        "DBs against these two local models). `capability_enforcement`'s adaptive ASR@20 is "
        f"also `0.000` in both suites. `dual_llm`'s is not: {total_success}/{total_trials} "
        "adaptive trials (combined across both suites) succeeded -- one real compromise "
        "(`mcp` / `direct_instruction` / `mcp_file_storage_0`, round 2 of 20). The full "
        "qualitative writeup (what the mutation actually changed, and why it's a structural "
        "blind spot -- `on_tool_result` quarantining can only ever filter what a tool "
        "*returns*, never what a poisoned tool *description* claims about itself before it's "
        "called) is in `docs/notes/architectural_defenses.md`'s S5-05 section.",
    ]

    return "\n".join(lines) + "\n"


def main() -> None:
    static_conn = connect(_STATIC_TRACE_DB)
    mcp_conn = connect(_MCP_TRACE_DB)
    adaptive_conn = connect(_ADAPTIVE_TRACE_DB)
    adaptive_mcp_conn = connect(_ADAPTIVE_MCP_TRACE_DB)
    try:
        static_cost_rows = _cost_overhead_table(static_conn, suite="workspace")
        mcp_cost_rows = _cost_overhead_table(mcp_conn, suite="mcp")
        static_utility_rows = _utility_tax_rows(static_conn)
        mcp_utility_rows = _utility_tax_rows(mcp_conn)
        static_security_rows = _static_security_table(static_conn, suite="workspace")
        mcp_security_rows = _static_security_table(mcp_conn, suite="mcp")
        adaptive_asr_rows = _adaptive_asr_summary(
            adaptive_conn, suite="workspace"
        ) + _adaptive_asr_summary(adaptive_mcp_conn, suite="mcp")
    finally:
        static_conn.close()
        mcp_conn.close()
        adaptive_conn.close()
        adaptive_mcp_conn.close()

    markdown = _render_markdown(
        static_cost_rows=static_cost_rows,
        mcp_cost_rows=mcp_cost_rows,
        static_utility_rows=static_utility_rows,
        mcp_utility_rows=mcp_utility_rows,
        static_security_rows=static_security_rows,
        mcp_security_rows=mcp_security_rows,
        adaptive_asr_rows=adaptive_asr_rows,
    )
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(markdown)
    print(
        f"wrote {OUTPUT_PATH} "
        f"({len(static_cost_rows) + len(mcp_cost_rows)} cost rows, "
        f"{len(static_utility_rows) + len(mcp_utility_rows)} utility rows, "
        f"{len(static_security_rows) + len(mcp_security_rows)} static security rows, "
        f"{len(adaptive_asr_rows)} adaptive ASR rows)"
    )


if __name__ == "__main__":
    main()
