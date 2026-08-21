"""S6-03 -- auto-generate `results/composition.md` from the S6-02
composition sweep: observed vs. independence-predicted ASR for every
composite pair, the utility tax each pair pays relative to each of its own
two components, and a per-layer cost/latency roll-up (S6-01's per-member
trace attribution makes this a direct query -- each composite episode's
`cost_record` rows are already labeled by the specific member that spent
the money, not by the composite run label). Mirrors
`scripts/generate_adaptive_results.py`'s pattern: typed row dataclasses,
one query function each, deterministic output, no new dependency.

Usage: .venv/bin/python scripts/generate_composition_results.py
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
OUTPUT_PATH = Path("results/composition.md")

# Independence/utility/cost analysis below is computed from *static*
# episodes only -- that's where the one non-zero ASR data point in this
# project (the S6-03 Groq slice, `configs/composition_groq_slice.yaml`)
# lives; the adaptive composition grid (S6-02) found ASR@20 = 0.000 in
# every cell against the local models (no adaptive Groq trials were run --
# prohibitively expensive), so an adaptive independence table here would
# carry zero additional signal. Real adaptive run numbers (including the
# one documented timeout failure) are in `docs/notes/composition.md`'s
# "S6-02 notes" run log, not duplicated here.

# S6-01's top-4 pick (`docs/notes/composition.md`'s D2) and the 8 composite
# names S6-02 actually ran (2 order-dependent pairs x both orderings, 4
# order-independent pairs x one ordering -- see that same note's "S6-02
# notes" section for which is which and why).
_TOP_FOUR = ("spotlighting", "instructional_prevention", "guard_model", "tool_allowlist")
_COMPOSITE_PAIRS = (
    "spotlighting+instructional_prevention",
    "instructional_prevention+spotlighting",
    "spotlighting+guard_model",
    "guard_model+spotlighting",
    "spotlighting+tool_allowlist",
    "instructional_prevention+guard_model",
    "instructional_prevention+tool_allowlist",
    "guard_model+tool_allowlist",
)
_ALL_DEFENSES = (*_TOP_FOUR, *_COMPOSITE_PAIRS)


@dataclass
class SecurityRow:
    suite: str
    defense: str
    attack: str
    model: str
    n_episodes: int
    asr: float


def _security_table(conn: sqlite3.Connection, *, suite: str) -> list[SecurityRow]:
    """Identical shape to `generate_static_baseline.py::_security_table`,
    restricted to the top-4 solo defenses (needed as the independence
    formula's inputs) plus the 8 composite names."""
    placeholders = ",".join("?" * len(_ALL_DEFENSES))
    rows = conn.execute(
        f"""
        SELECT r.defense_stack AS defense, r.attack AS attack, r.model AS model,
               e.security AS security
        FROM episode e JOIN run r ON e.run_id = r.id
        WHERE e.injection_task_id IS NOT NULL AND r.defense_stack IN ({placeholders})
        """,
        _ALL_DEFENSES,
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
class IndependenceRow:
    suite: str
    attack: str
    model: str
    pair: str
    member_a: str
    member_b: str
    asr_a: float | None
    n_a: int
    asr_b: float | None
    n_b: int
    predicted: float | None
    observed: float | None
    n_observed: int
    delta: float | None  # observed - predicted; positive = underperforms independence


def _independence_table(security_rows: list[SecurityRow]) -> list[IndependenceRow]:
    """Independence-assumption prediction, using the exact formula S6-03's
    own task spec mandates: treating each defense's own ASR (measured on
    the *same* suite/attack/model population, never a different sweep's
    numbers) as the probability an attack succeeds against that defense in
    isolation, and assuming the two members' susceptibility to a given
    attack are independent random events, the standard probability-of-a-
    union formula predicts the joint ASR when both are deployed together:

        predicted = ASR_A + ASR_B - ASR_A * ASR_B

    This is not the only defensible composition model -- a strict-AND
    "the attacker must evade both layers to succeed" assumption would
    instead predict `ASR_A * ASR_B` -- but it is the specific formula this
    task specifies, implemented exactly as written rather than re-derived,
    since a silently-wrong formula would undermine the whole analysis.

    `None` (never `0.0`) whenever a needed cell has no data at all -- a
    missing measurement must never silently read as "0% ASR confirmed".
    """
    by_key = {(r.defense, r.attack, r.model): r for r in security_rows}
    populations = sorted({(r.suite, r.attack, r.model) for r in security_rows})

    rows = []
    for suite, attack, model in populations:
        for pair in _COMPOSITE_PAIRS:
            member_a, member_b = pair.split("+", 1)
            observed_row = by_key.get((pair, attack, model))
            a_row = by_key.get((member_a, attack, model))
            b_row = by_key.get((member_b, attack, model))
            if observed_row is None and a_row is None and b_row is None:
                continue  # nothing measured for this pair on this population at all

            predicted = (
                a_row.asr + b_row.asr - a_row.asr * b_row.asr
                if a_row is not None and b_row is not None
                else None
            )
            observed = observed_row.asr if observed_row is not None else None
            rows.append(
                IndependenceRow(
                    suite=suite,
                    attack=attack,
                    model=model,
                    pair=pair,
                    member_a=member_a,
                    member_b=member_b,
                    asr_a=a_row.asr if a_row is not None else None,
                    n_a=a_row.n_episodes if a_row is not None else 0,
                    asr_b=b_row.asr if b_row is not None else None,
                    n_b=b_row.n_episodes if b_row is not None else 0,
                    predicted=predicted,
                    observed=observed,
                    n_observed=observed_row.n_episodes if observed_row is not None else 0,
                    delta=(observed - predicted)
                    if observed is not None and predicted is not None
                    else None,
                )
            )
    return rows


def _utility_tax_rows_for_pairs(conn: sqlite3.Connection) -> list[UtilityTaxRow]:
    """Each composite pair's utility rate vs. *each of its own two
    components'* -- not vs. `no_defense` -- directly answering "does
    composing A+B cost more utility than either A or B alone?". Reuses
    S5-02's `utility_tax_table`/`benign_utility_rate` unmodified."""
    rates = benign_utility_rate(conn)
    rows = []
    for pair in _COMPOSITE_PAIRS:
        member_a, member_b = pair.split("+", 1)
        rows += utility_tax_table(rates, baseline=member_a, target=pair)
        rows += utility_tax_table(rates, baseline=member_b, target=pair)
    return rows


@dataclass
class MemberCostRow:
    suite: str
    pair: str
    model: str
    member: str
    n_episodes: int
    mean_wall_ms: float
    mean_usd: float


def _member_cost_table(conn: sqlite3.Connection, *, suite: str) -> list[MemberCostRow]:
    """Per composite pair, the mean per-episode wall_ms/$ *each member*
    contributes -- a direct query only because S6-01 already labels every
    `cost_record` row by the specific member that made the call
    (`defense:<member name>`), not by the composite run label. Members
    that never make a model call (`spotlighting`, `instructional_prevention`,
    `tool_allowlist` -- pure regex/text-transform/policy-check, all $0 by
    construction) simply never appear here; only `guard_model` does."""
    placeholders = ",".join("?" * len(_COMPOSITE_PAIRS))
    rows = conn.execute(
        f"""
        SELECT r.defense_stack AS pair, r.model AS model, e.id AS episode_id,
               cr.model AS cost_model, cr.wall_ms AS wall_ms, cr.usd AS usd
        FROM episode e JOIN run r ON e.run_id = r.id
        JOIN cost_record cr ON cr.episode_id = e.id
        WHERE r.defense_stack IN ({placeholders}) AND cr.model LIKE 'defense:%'
        """,
        _COMPOSITE_PAIRS,
    ).fetchall()

    per_episode_wall: dict[tuple[str, str, str, int], float] = defaultdict(float)
    per_episode_usd: dict[tuple[str, str, str, int], float] = defaultdict(float)
    episodes_by_group: dict[tuple[str, str, str], set[int]] = defaultdict(set)

    for row in rows:
        member = row["cost_model"].removeprefix("defense:")
        group = (row["pair"], row["model"], member)
        key = (*group, row["episode_id"])
        per_episode_wall[key] += row["wall_ms"]
        per_episode_usd[key] += row["usd"]
        episodes_by_group[group].add(row["episode_id"])

    result = []
    for (pair, model, member), episode_ids in sorted(episodes_by_group.items()):
        wall_values = [per_episode_wall[(pair, model, member, eid)] for eid in episode_ids]
        usd_values = [per_episode_usd[(pair, model, member, eid)] for eid in episode_ids]
        result.append(
            MemberCostRow(
                suite=suite,
                pair=pair,
                model=model,
                member=member,
                n_episodes=len(episode_ids),
                mean_wall_ms=statistics.mean(wall_values),
                mean_usd=statistics.mean(usd_values),
            )
        )
    return result


def _render_markdown(
    *,
    security_rows: list[SecurityRow],
    independence_rows: list[IndependenceRow],
    utility_rows: list[UtilityTaxRow],
    cost_rows: list[MemberCostRow],
) -> str:
    lines = [
        "# Composition Results (S6-01 .. S6-03)",
        "",
        f"Auto-generated by `scripts/generate_composition_results.py` against "
        f"`{_STATIC_TRACE_DB}`, `{_MCP_TRACE_DB}` on {datetime.now(UTC).isoformat()}. "
        "Every number below comes from a query against a trace DB -- never hand-edited; "
        "re-run the script to refresh after a new sweep.",
        "",
        "## Security: observed vs. independence-predicted ASR, by (suite, attack, model, pair)",
        "",
        "`predicted = ASR_A + ASR_B - ASR_A * ASR_B` (see "
        "`_independence_table`'s docstring for exactly what this formula assumes and why "
        "it -- not a strict-AND `ASR_A * ASR_B` model -- is the one computed here). `delta "
        "= observed - predicted`: positive means the composed pair let *more* attacks "
        "through than independence predicts (underperforms independence); negative means "
        "fewer. `n/a` means a needed cell has no data, not a confirmed `0.000`.",
        "",
        "| Suite | Attack | Model | Pair | ASR(A) | ASR(B) | Predicted | Observed | Delta |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in independence_rows:
        asr_a = f"{row.asr_a:.3f} ({row.n_a})" if row.asr_a is not None else "n/a"
        asr_b = f"{row.asr_b:.3f} ({row.n_b})" if row.asr_b is not None else "n/a"
        predicted = f"{row.predicted:.3f}" if row.predicted is not None else "n/a"
        observed = f"{row.observed:.3f} ({row.n_observed})" if row.observed is not None else "n/a"
        delta = f"{row.delta:+.3f}" if row.delta is not None else "n/a"
        lines.append(
            f"| {row.suite} | {row.attack} | {row.model} | {row.pair} | {asr_a} | {asr_b} "
            f"| {predicted} | {observed} | {delta} |"
        )

    notable_deltas = sorted(
        (
            r
            for r in independence_rows
            if r.delta is not None and abs(r.delta) > 0.001 and r.n_a > 0 and r.n_b > 0
        ),
        key=lambda r: -abs(r.delta) if r.delta is not None else 0,
    )
    lines += [
        "",
        "## Notable deltas: pairs where observed ASR differs from the independence prediction",
        "",
        "Every row above with a non-zero `delta` and real (non-`n/a`) data on both sides -- "
        "the direct answer to \"did composing beat or undershoot the naive independence "
        "prediction\", surfaced explicitly rather than left buried in the full table.",
        "",
    ]
    if notable_deltas:
        for row in notable_deltas:
            direction = (
                "OUTPERFORMED" if row.delta is not None and row.delta < 0 else "underperformed"
            )
            lines.append(
                f"- **`{row.pair}`** / `{row.attack}` / `{row.model}` (suite `{row.suite}`): "
                f"observed ASR {row.observed:.3f} vs. predicted {row.predicted:.3f} "
                f"(delta {row.delta:+.3f}) -- the composed pair **{direction}** the "
                f"independence prediction. `{row.member_a}` alone: {row.asr_a:.3f}; "
                f"`{row.member_b}` alone: {row.asr_b:.3f}."
            )
    else:
        lines.append("None -- every measured pair's observed ASR matched its prediction exactly.")

    non_trivial = [
        r
        for r in independence_rows
        if r.observed is not None
        and r.observed > 0
        and (r.asr_a in (0.0, None))
        and (r.asr_b in (0.0, None))
    ]
    lines += [
        "",
        "## Real interaction check: composed ASR > 0 where *neither* solo member's was",
        "",
        "The finding worth reporting even as a single cell, per this project's established "
        '"report the real exception, don\'t average it away" precedent (Sprint 5\'s `dual_llm` '
        "adaptive success is the direct model for this).",
        "",
    ]
    if non_trivial:
        for row in non_trivial:
            lines.append(
                f"- **`{row.pair}`** / `{row.attack}` / `{row.model}` (suite `{row.suite}`): "
                f"observed ASR {row.observed:.3f} ({row.n_observed} episodes), vs. "
                f"`{row.member_a}` alone ASR={row.asr_a}, `{row.member_b}` alone ASR={row.asr_b}."
            )
    else:
        lines.append(
            "None found. Every composite pair's observed ASR is explained by (or is lower "
            "than) at least one of its own two components' solo ASR on the same population -- "
            "no pair produced a compromise that neither solo defense already showed."
        )

    lines += [
        "",
        "## Utility tax: each composite pair vs. each of its own two components",
        "",
        "Injection-free episodes only (`scoring.utility.benign_utility_rate`). "
        "`baseline_defense` is one of the pair's own two members, not `no_defense` -- this "
        "answers \"does composing cost more utility than either component alone?\", not "
        '"vs. no defense at all" (S5-02/S5-06 already answer that question for the '
        "architectural defenses).",
        "",
        "| Model | Pair | vs. | Component rate (n) | Pair rate (n) | Tax |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for util_row in utility_rows:
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
            f"| {util_row.model} | {util_row.target_defense} | {util_row.baseline_defense} "
            f"| {base} | {tgt} | {tax} |"
        )

    lines += [
        "",
        "## Cost & latency: per-member overhead inside each composite pair",
        "",
        "Mean per-episode wall_ms/$ *each member* contributes, attributed correctly to that "
        "member even though the episode ran under the composite's own label (S6-01). Only "
        "`guard_model` ever appears -- the other 3 of the top-4 make no model calls, by "
        "construction.",
        "",
        "| Suite | Pair | Model | Member | Episodes | Mean $ | Mean ms |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for cost_row in cost_rows:
        lines.append(
            f"| {cost_row.suite} | {cost_row.pair} | {cost_row.model} | {cost_row.member} "
            f"| {cost_row.n_episodes} | {cost_row.mean_usd:.6f} | {cost_row.mean_wall_ms:.0f} |"
        )

    return "\n".join(lines) + "\n"


def main() -> None:
    static_conn = connect(_STATIC_TRACE_DB)
    mcp_conn = connect(_MCP_TRACE_DB)
    try:
        static_security = _security_table(static_conn, suite="workspace")
        mcp_security = _security_table(mcp_conn, suite="mcp")
        security_rows = static_security + mcp_security

        independence_rows = _independence_table(security_rows)

        utility_rows = _utility_tax_rows_for_pairs(static_conn) + _utility_tax_rows_for_pairs(
            mcp_conn
        )

        cost_rows = _member_cost_table(static_conn, suite="workspace") + _member_cost_table(
            mcp_conn, suite="mcp"
        )
    finally:
        static_conn.close()
        mcp_conn.close()

    markdown = _render_markdown(
        security_rows=security_rows,
        independence_rows=independence_rows,
        utility_rows=utility_rows,
        cost_rows=cost_rows,
    )
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(markdown)
    print(
        f"wrote {OUTPUT_PATH} "
        f"({len(security_rows)} security rows, {len(independence_rows)} independence rows, "
        f"{len(utility_rows)} utility rows, {len(cost_rows)} cost rows)"
    )


if __name__ == "__main__":
    main()
