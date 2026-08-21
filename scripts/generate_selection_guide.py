"""S7-02 -- defense selection guide: one table synthesizing every solo
defense's real static ASR (local models, and L5 where this project ever
measured it), utility tax vs. `no_defense`, and defense-only cost/latency
overhead -- pulled fresh from the trace DB, never hand-transcribed from an
existing `results/*.md` file (this project's standing discipline, S7-01's
own note included). Numbers only; the "when to use / when to avoid"
judgment and the S6-04 composition caveat are genuine prose, not
derivable from a query, and live in `docs/notes/release.md`'s own S7-02
section instead (same numbers-vs-judgment split S6-06 already established).

Usage: .venv/bin/python scripts/generate_selection_guide.py
"""

from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from injection_pareto.trace import connect

_TRACE_DB = Path("runs/local/static_sweep/trace.db")
OUTPUT_PATH = Path("results/selection_guide.md")

_LOCAL_MODELS = ("llama3.2:3b", "llama3.1:latest")
_L5_MODEL = "openai/gpt-oss-120b"

# Every solo defense this project has ever registered (`no_defense` kept as
# the zero-point baseline, not a real recommendation) -- D2's own
# mechanism-family framing (`docs/notes/composition.md`), restated here as
# the guide's own explicit, hardcoded categorization (not derivable from a
# query -- it's a property of each defense's implementation, not its data).
_DEFENSES: tuple[tuple[str, str], ...] = (
    ("no_defense", "baseline"),
    ("spotlighting", "prompt-level"),
    ("instructional_prevention", "prompt-level"),
    ("guard_model", "detection-only (D4)"),
    ("canary", "detection-only"),
    ("tool_allowlist", "architectural hard block"),
    ("capability_enforcement", "architectural hard block (D8/CaMeL-style)"),
    ("dual_llm", "architectural quarantine (D7)"),
)


@dataclass
class GuideRow:
    defense: str
    mechanism_family: str
    local_asr: float | None
    n_local_asr: int
    l5_asr: float | None
    n_l5_asr: int
    utility_tax: float | None
    n_utility: int
    mean_defense_usd: float
    mean_defense_ms: float


def _local_asr_by_defense(conn: sqlite3.Connection) -> dict[str, tuple[float, int]]:
    """Mean ASR across every attack family and both local models -- this
    project's own most-measured static population, and (per every prior
    sprint's finding) uniformly `0.000` here for every defense. Reported
    plainly as such, not omitted for being uninteresting."""
    placeholders = ",".join("?" * len(_LOCAL_MODELS))
    rows = conn.execute(
        f"""
        SELECT r.defense_stack AS defense, e.security AS security
        FROM episode e JOIN run r ON e.run_id = r.id
        WHERE r.model IN ({placeholders}) AND e.injection_task_id IS NOT NULL
        """,
        _LOCAL_MODELS,
    ).fetchall()
    grouped: dict[str, list[bool]] = defaultdict(list)
    for row in rows:
        grouped[row["defense"]].append(bool(row["security"]))
    return {d: (sum(v) / len(v), len(v)) for d, v in grouped.items()}


def _l5_asr_by_defense(conn: sqlite3.Connection) -> dict[str, tuple[float, int]]:
    """L5 (`openai/gpt-oss-120b`), `important_instructions` only -- the one
    attack family this project has ever run against a paid model, and the
    only place any of these defenses show a non-`0.000` static ASR
    (S2-11, S6-03's `configs/composition_groq_slice.yaml`). Most defenses
    were never run on L5 at all -- callers must treat a missing key as
    `n/a`, not `0.000`."""
    rows = conn.execute(
        """
        SELECT r.defense_stack AS defense, e.security AS security
        FROM episode e JOIN run r ON e.run_id = r.id
        WHERE r.model = ? AND r.attack = 'important_instructions'
          AND e.injection_task_id IS NOT NULL
        """,
        (_L5_MODEL,),
    ).fetchall()
    grouped: dict[str, list[bool]] = defaultdict(list)
    for row in rows:
        grouped[row["defense"]].append(bool(row["security"]))
    return {d: (sum(v) / len(v), len(v)) for d, v in grouped.items()}


def _local_utility_by_defense(conn: sqlite3.Connection) -> dict[str, tuple[float, int]]:
    """Mean benign utility rate per defense across the two local models --
    mirrors `generate_pareto_plot.py::_local_utility_by_defense` (small
    enough, and different enough in what it's paired with here, to
    duplicate rather than import -- same precedent as
    `generate_guard_model_roc.py`'s own duplicated `_rank`/`_pearson`)."""
    placeholders = ",".join("?" * len(_LOCAL_MODELS))
    rows = conn.execute(
        f"""
        SELECT r.defense_stack AS defense, e.id AS episode_id, e.utility AS utility
        FROM episode e JOIN run r ON e.run_id = r.id
        WHERE r.model IN ({placeholders}) AND e.injection_task_id IS NULL
          AND e.utility IS NOT NULL
        """,
        _LOCAL_MODELS,
    ).fetchall()
    grouped: dict[str, list[bool]] = defaultdict(list)
    for row in rows:
        grouped[row["defense"]].append(bool(row["utility"]))
    return {d: (statistics.mean(v), len(v)) for d, v in grouped.items()}


def _defense_overhead_by_defense(conn: sqlite3.Connection) -> dict[str, tuple[float, float]]:
    """Mean $/episode and mean ms/episode contributed by the defense's own
    calls (`cost_record.model` labeled `defense:<name>`), local models
    only -- summed per episode first, then averaged across episodes (an
    episode with more defense calls has more `cost_record` rows; averaging
    raw rows would silently misweight by call count, the same fix
    `generate_pareto_plot.py::_mean_cost_by_defense` already applied). A
    defense absent from the result made zero extra calls -- structurally
    `$0`/`0ms`, not missing data (`capability_enforcement`/`tool_allowlist`/
    `spotlighting`/`instructional_prevention`/`canary` are pure regex/
    prompt-transform/policy-check, no model calls, by construction)."""
    placeholders = ",".join("?" * len(_LOCAL_MODELS))
    rows = conn.execute(
        f"""
        SELECT r.defense_stack AS defense, e.id AS episode_id, cr.usd AS usd, cr.wall_ms AS wall_ms
        FROM cost_record cr JOIN episode e ON cr.episode_id = e.id JOIN run r ON e.run_id = r.id
        WHERE r.model IN ({placeholders}) AND cr.model LIKE 'defense:%'
        """,
        _LOCAL_MODELS,
    ).fetchall()
    per_episode_usd: dict[tuple[str, int], float] = defaultdict(float)
    per_episode_ms: dict[tuple[str, int], float] = defaultdict(float)
    episodes_by_defense: dict[str, set[int]] = defaultdict(set)
    for row in rows:
        key = (row["defense"], row["episode_id"])
        per_episode_usd[key] += row["usd"]
        per_episode_ms[key] += row["wall_ms"]
        episodes_by_defense[row["defense"]].add(row["episode_id"])

    result = {}
    for defense, episode_ids in episodes_by_defense.items():
        usd_values = [per_episode_usd[(defense, eid)] for eid in episode_ids]
        ms_values = [per_episode_ms[(defense, eid)] for eid in episode_ids]
        result[defense] = (statistics.mean(usd_values), statistics.mean(ms_values))
    return result


def _build_guide(conn: sqlite3.Connection) -> list[GuideRow]:
    return _combine_guide_rows(
        local_asr=_local_asr_by_defense(conn),
        l5_asr=_l5_asr_by_defense(conn),
        utility=_local_utility_by_defense(conn),
        overhead=_defense_overhead_by_defense(conn),
    )


def _combine_guide_rows(
    *,
    local_asr: dict[str, tuple[float, int]],
    l5_asr: dict[str, tuple[float, int]],
    utility: dict[str, tuple[float, int]],
    overhead: dict[str, tuple[float, float]],
) -> list[GuideRow]:
    """Pure combination step, split out from `_build_guide` so it's
    directly unit-testable against fake fetched-data fixtures -- mirrors
    `generate_composition_results.py::_independence_table`'s own
    query-vs-compute split (query functions untested here, matching this
    project's standing precedent; this pure combiner is)."""
    no_defense_utility = utility.get("no_defense", (None, 0))[0]

    rows = []
    for defense, mechanism_family in _DEFENSES:
        la = local_asr.get(defense)
        l5a = l5_asr.get(defense)
        u = utility.get(defense)
        usd, ms = overhead.get(defense, (0.0, 0.0))
        tax = (
            no_defense_utility - u[0]
            if u is not None and no_defense_utility is not None
            else None
        )
        rows.append(
            GuideRow(
                defense=defense,
                mechanism_family=mechanism_family,
                local_asr=la[0] if la else None,
                n_local_asr=la[1] if la else 0,
                l5_asr=l5a[0] if l5a else None,
                n_l5_asr=l5a[1] if l5a else 0,
                utility_tax=tax,
                n_utility=u[1] if u else 0,
                mean_defense_usd=usd,
                mean_defense_ms=ms,
            )
        )
    return rows


def _render_markdown(rows: list[GuideRow]) -> str:
    lines = [
        "# Defense selection guide (S7-02)",
        "",
        f"Auto-generated by `scripts/generate_selection_guide.py` against `{_TRACE_DB}` on "
        f"{datetime.now(UTC).isoformat()}. Numbers only -- the \"when to use / when to avoid\" "
        "judgment, the S6-04 composition caveat, and the `dual_llm`-vs-`capability_enforcement` "
        "cost asymmetry are genuine prose, not derivable from a query; see "
        "`docs/notes/release.md`'s own S7-02 section for those. Every number below comes from a "
        "query against the trace DB -- never hand-edited; re-run the script to refresh.",
        "",
        "`Utility tax` = `no_defense`'s rate minus this defense's rate (matches "
        "`scoring.utility.UtilityTaxRow`'s own sign convention): positive means this defense "
        "completes *fewer* benign tasks than running nothing at all -- a real tax; negative "
        "means it did *better* than the baseline on this small sample, report that honestly too.",
        "",
        "| Defense | Mechanism | Local ASR (n) | L5 ASR (n) | Utility tax vs. no_defense (n) "
        "| Defense $/episode | Defense ms/episode |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        local_asr = (
            f"{row.local_asr:.3f} ({row.n_local_asr})" if row.local_asr is not None else "n/a"
        )
        l5_asr = f"{row.l5_asr:.3f} ({row.n_l5_asr})" if row.l5_asr is not None else "n/a"
        tax = f"{row.utility_tax:+.3f} ({row.n_utility})" if row.utility_tax is not None else "n/a"
        lines.append(
            f"| {row.defense} | {row.mechanism_family} | {local_asr} | {l5_asr} | {tax} "
            f"| {row.mean_defense_usd:.6f} | {row.mean_defense_ms:.0f} |"
        )

    lines += [
        "",
        "`Local ASR` is `0.000` for every defense (including `no_defense` itself) -- this "
        "project's local models have never once been compromised by any static attack family "
        "measured here, the uninformative-but-real result every prior sprint has already found. "
        "`L5 ASR` is the only place any real static security signal exists in this project "
        "(`important_instructions` against `openai/gpt-oss-120b`, n=3/cell); most defenses were "
        "never run on L5 at all (`n/a`, not a confirmed `0.000`) -- `docs/notes/composition.md`'s "
        "S6-02/S6-03 notes explain why (`guard_model`'s solo/composite Groq slice was skipped "
        "on cost grounds, its block being a structural no-op either way).",
        "",
        "`Defense $/episode` and `Defense ms/episode` are the defense's own incremental overhead "
        "(`cost_record` rows labeled `defense:<name>`) on top of the base agent call -- `0.000000`/"
        "`0` for `spotlighting`/`instructional_prevention`/`tool_allowlist`/`canary`/"
        "`capability_enforcement` is a structural fact (no model calls, by construction), not "
        "missing data. `guard_model`/`dual_llm` show real nonzero latency (their own local "
        "classifier/quarantine calls) but `$0` cost specifically because this project's guard "
        "and quarantine models are both local Ollama models, not because overhead is free in "
        "general -- see `docs/notes/release.md`.",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    conn = connect(_TRACE_DB)
    try:
        rows = _build_guide(conn)
    finally:
        conn.close()

    markdown = _render_markdown(rows)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(markdown)
    print(f"wrote {OUTPUT_PATH} ({len(rows)} defense rows)")


if __name__ == "__main__":
    main()
