"""S7-01 -- the Pareto plot: security (ASR) x utility retention x cost
($/episode). See `docs/notes/release.md` for why this is two panels, not
one plotting the sprint plan's own literal "adaptive ASR" spec -- adaptive
ASR@20 is `0.000` in all 108 real (suite, defense, attack family, model)
cells this project has ever measured (360 underlying trials)
(`results/adaptive.md`), so panel 1 is that honest null result, and panel 2
substitutes the one place real ASR variation exists (`openai/gpt-oss-120b`
via `configs/composition_groq_slice.yaml`), both explicitly labeled. Both
panels borrow the local-model benign utility rate as the Y axis (neither
adaptive trials nor the L5 slice measure utility for most of their own
defenses -- `docs/notes/release.md` explains why).

Mirrors this project's established `generate_*.py` pattern: typed row
dataclasses, one query function each, deterministic output. Reuses
`generate_adaptive_results.py`'s `_asr_at_20`/`_defense_level_asr`/`AsrRow`
via the same `importlib`-from-file-path technique the test suite already
uses for cross-script reuse, since `scripts/` isn't an importable package.

Usage: .venv/bin/python scripts/generate_pareto_plot.py
"""

from __future__ import annotations

import importlib.util
import sqlite3
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402 -- must follow matplotlib.use("Agg")

from injection_pareto.scoring import benign_utility_rate  # noqa: E402
from injection_pareto.trace import connect  # noqa: E402

_ADAPTIVE_RESULTS_PATH = Path(__file__).parent / "generate_adaptive_results.py"
_spec = importlib.util.spec_from_file_location(
    "generate_adaptive_results_for_pareto", _ADAPTIVE_RESULTS_PATH
)
assert _spec is not None and _spec.loader is not None
_generate_adaptive_results = importlib.util.module_from_spec(_spec)
sys.modules["generate_adaptive_results_for_pareto"] = _generate_adaptive_results
_spec.loader.exec_module(_generate_adaptive_results)

_asr_at_20 = _generate_adaptive_results._asr_at_20
_defense_level_asr = _generate_adaptive_results._defense_level_asr

_STATIC_TRACE_DB = Path("runs/local/static_sweep/trace.db")
_ADAPTIVE_TRACE_DB = Path("runs/local/adaptive_sweep/trace.db")

OUTPUT_IMAGE_PATH = Path("results/pareto.png")
OUTPUT_MD_PATH = Path("results/pareto.md")

_LOCAL_MODELS = ("llama3.2:3b", "llama3.1:latest")

# Panel 1 (adaptive, real null result) -- `results/adaptive.md`'s own
# workspace-suite defense grid.
_ADAPTIVE_DEFENSES = (
    "no_defense",
    "spotlighting",
    "instructional_prevention",
    "guard_model",
    "tool_allowlist",
    "canary",
)

# Panel 2 (static, L5/Groq -- the one place real ASR variation exists) --
# exactly `configs/composition_groq_slice.yaml`'s defense set plus
# `no_defense` (S2-11's own Groq slice). `guard_model` and its composites
# are absent -- that slice deliberately excluded them (docs/notes/release.md).
_L5_MODEL = "openai/gpt-oss-120b"
_L5_DEFENSES = (
    "no_defense",
    "spotlighting",
    "instructional_prevention",
    "tool_allowlist",
    "spotlighting+instructional_prevention",
    "instructional_prevention+tool_allowlist",
)


@dataclass
class ParetoPoint:
    defense: str
    asr: float
    n_asr: int
    utility: float
    n_utility: int
    mean_usd: float


def _local_utility_by_defense(conn: sqlite3.Connection) -> dict[str, tuple[float, int]]:
    """Mean benign utility rate per defense, averaged (unweighted, matching
    `generate_adaptive_results.py::_defense_level_asr`'s own precedent)
    across this project's two local models -- reused as the utility axis
    for both panels (docs/notes/release.md's E1 resolution)."""
    rates = benign_utility_rate(conn)
    by_defense: dict[str, list[float]] = defaultdict(list)
    n_by_defense: dict[str, int] = defaultdict(int)
    for r in rates:
        if r.model in _LOCAL_MODELS:
            by_defense[r.defense].append(r.rate)
            n_by_defense[r.defense] += r.n_episodes
    return {
        defense: (statistics.mean(values), n_by_defense[defense])
        for defense, values in by_defense.items()
    }


def _mean_cost_by_defense(
    conn: sqlite3.Connection, *, models: tuple[str, ...]
) -> dict[str, float]:
    """Mean $/episode per defense, for episodes run on any of `models` --
    summed per episode first, then averaged across episodes (an episode
    with more tool calls has more `cost_record` rows; averaging raw rows
    instead of per-episode totals would silently misweight by call count)."""
    placeholders = ",".join("?" * len(models))
    rows = conn.execute(
        f"""
        SELECT r.defense_stack AS defense, e.id AS episode_id, cr.usd AS usd
        FROM cost_record cr JOIN episode e ON cr.episode_id = e.id JOIN run r ON e.run_id = r.id
        WHERE r.model IN ({placeholders})
        """,
        models,
    ).fetchall()
    per_episode: dict[tuple[str, int], float] = defaultdict(float)
    for row in rows:
        per_episode[(row["defense"], row["episode_id"])] += row["usd"]
    by_defense: dict[str, list[float]] = defaultdict(list)
    for (defense, _episode_id), total in per_episode.items():
        by_defense[defense].append(total)
    return {defense: statistics.mean(values) for defense, values in by_defense.items()}


def _l5_asr_by_defense(conn: sqlite3.Connection) -> dict[str, tuple[float, int]]:
    placeholders = ",".join("?" * len(_L5_DEFENSES))
    rows = conn.execute(
        f"""
        SELECT r.defense_stack AS defense, e.security AS security
        FROM episode e JOIN run r ON e.run_id = r.id
        WHERE r.model = ? AND r.defense_stack IN ({placeholders})
          AND e.injection_task_id IS NOT NULL
        """,
        (_L5_MODEL, *_L5_DEFENSES),
    ).fetchall()
    grouped: dict[str, list[bool]] = defaultdict(list)
    for row in rows:
        grouped[row["defense"]].append(bool(row["security"]))
    return {
        defense: (sum(values) / len(values), len(values)) for defense, values in grouped.items()
    }


def _adaptive_panel_points(
    static_conn: sqlite3.Connection, adaptive_conn: sqlite3.Connection
) -> list[ParetoPoint]:
    asr20_rows = [
        r
        for r in _asr_at_20(adaptive_conn)
        if r.suite == "workspace" and r.defense in _ADAPTIVE_DEFENSES
    ]
    asr_by_defense = _defense_level_asr(asr20_rows)
    n_by_defense: dict[str, int] = defaultdict(int)
    for r in asr20_rows:
        n_by_defense[r.defense] += r.n

    utility_by_defense = _local_utility_by_defense(static_conn)
    cost_by_defense = _mean_cost_by_defense(adaptive_conn, models=_LOCAL_MODELS)

    points = []
    for defense in _ADAPTIVE_DEFENSES:
        if defense not in asr_by_defense or defense not in utility_by_defense:
            continue  # real data missing for this defense -- skip, don't fabricate
        utility, n_utility = utility_by_defense[defense]
        points.append(
            ParetoPoint(
                defense=defense,
                asr=asr_by_defense[defense],
                n_asr=n_by_defense[defense],
                utility=utility,
                n_utility=n_utility,
                mean_usd=cost_by_defense.get(defense, 0.0),
            )
        )
    return points


def _l5_panel_points(static_conn: sqlite3.Connection) -> list[ParetoPoint]:
    asr_by_defense = _l5_asr_by_defense(static_conn)
    utility_by_defense = _local_utility_by_defense(static_conn)
    cost_by_defense = _mean_cost_by_defense(static_conn, models=(_L5_MODEL,))

    points = []
    for defense in _L5_DEFENSES:
        if defense not in asr_by_defense or defense not in utility_by_defense:
            continue
        asr, n_asr = asr_by_defense[defense]
        utility, n_utility = utility_by_defense[defense]
        points.append(
            ParetoPoint(
                defense=defense,
                asr=asr,
                n_asr=n_asr,
                utility=utility,
                n_utility=n_utility,
                mean_usd=cost_by_defense.get(defense, 0.0),
            )
        )
    return points


def pareto_frontier(points: list[ParetoPoint]) -> list[ParetoPoint]:
    """Non-dominated set: lower ASR is better, higher utility is better.
    `p` is dominated iff some other point `q` is at-least-as-good on both
    axes and strictly better on at least one -- an exact tie doesn't
    dominate its own twin, both stay on the frontier."""
    frontier = []
    for p in points:
        dominated = any(
            q is not p
            and q.asr <= p.asr
            and q.utility >= p.utility
            and (q.asr < p.asr or q.utility > p.utility)
            for q in points
        )
        if not dominated:
            frontier.append(p)
    return frontier


def _render_plot(
    adaptive_points: list[ParetoPoint], l5_points: list[ParetoPoint]
) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 5.5))

    for ax, points, title, cost_note in (
        (
            ax1,
            adaptive_points,
            "Panel 1 -- adaptive ASR@20 (workspace, local models)\n"
            "real null result: every point at ASR=0",
            "bubble size uniform: local inference is $0",
        ),
        (
            ax2,
            l5_points,
            "Panel 2 -- static ASR (workspace, L5/openai/gpt-oss-120b)\n"
            "the one place real ASR variation exists",
            "bubble size = mean $/episode on L5",
        ),
    ):
        frontier = {p.defense for p in pareto_frontier(points)}
        # Bubble size scaled for visibility -- $/episode here is small
        # (~$0-0.0003); an arbitrary but documented linear scale, not a
        # literal pixel-per-dollar mapping.
        sizes = [200 + p.mean_usd * 2_000_000 for p in points]
        colors = ["#d95f02" if p.defense in frontier else "#7570b3" for p in points]
        ax.scatter(
            [p.asr for p in points],
            [p.utility for p in points],
            s=sizes,
            c=colors,
            alpha=0.75,
            edgecolors="black",
            linewidths=0.8,
            zorder=3,
        )
        frontier_sorted = sorted(pareto_frontier(points), key=lambda p: p.asr)
        if len(frontier_sorted) > 1:
            ax.plot(
                [p.asr for p in frontier_sorted],
                [p.utility for p in frontier_sorted],
                color="#d95f02",
                linestyle="--",
                linewidth=1.2,
                zorder=2,
            )
        # Several defenses genuinely tie on (asr, utility) -- real ties in
        # the data (e.g. panel 1's `guard_model`/`tool_allowlist`/
        # `no_defense` all sit at exactly (0.0, 0.333)), not an artifact.
        # Stack their labels vertically instead of letting them overlap
        # into unreadable text.
        seen_at: dict[tuple[float, float], int] = defaultdict(int)
        for p in sorted(points, key=lambda p: p.defense):
            key = (p.asr, p.utility)
            row = seen_at[key]
            seen_at[key] += 1
            # Long composite names ("a+b") wrap onto a second line instead
            # of extending far enough right to collide with a neighboring
            # point's own label -- purely a text-layout choice, doesn't
            # change what's plotted.
            label = p.defense.replace("+", "+\n")
            ax.annotate(
                label,
                (p.asr, p.utility),
                textcoords="offset points",
                xytext=(10, 6 + row * 11),
                fontsize=7.5 if "+" in p.defense else 8,
            )
        ax.set_xlabel("Attack success rate (lower is better)")
        ax.set_ylabel("Benign utility retention, local models (higher is better)")
        ax.set_title(title, fontsize=10)
        ax.set_xlim(-0.05, 1.0)
        ax.set_ylim(-0.05, 1.05)
        ax.grid(True, alpha=0.3)
        ax.text(
            0.98,
            0.02,
            cost_note,
            transform=ax.transAxes,
            ha="right",
            va="bottom",
            fontsize=7,
            color="#555555",
        )

    fig.suptitle(
        "Defense Pareto frontier: security x utility x cost\n"
        "orange = non-dominated (Pareto-frontier) defense",
        fontsize=11,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    OUTPUT_IMAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT_IMAGE_PATH, dpi=150)
    plt.close(fig)


def _render_markdown(
    adaptive_points: list[ParetoPoint], l5_points: list[ParetoPoint]
) -> str:
    lines = [
        "# Pareto plot (S7-01)",
        "",
        "Auto-generated by `scripts/generate_pareto_plot.py` against "
        f"`{_STATIC_TRACE_DB}`, `{_ADAPTIVE_TRACE_DB}` on {datetime.now(UTC).isoformat()}. "
        "Why this is two panels, not one plotting the sprint plan's literal \"adaptive ASR\" "
        "spec, and why both panels' utility axis comes from local-model data even for the L5 "
        "panel, is decided and documented in `docs/notes/release.md` -- read that first. "
        "Every number below comes from a query against a trace DB -- never hand-edited; "
        "re-run the script to refresh.",
        "",
        f"![Pareto plot]({OUTPUT_IMAGE_PATH.name})",
        "",
        "## Panel 1 -- adaptive ASR@20, workspace suite, local models",
        "",
        "The honest result: 20 rounds of LLM-driven payload mutation found **zero** "
        "compromises against any of these 6 defenses on this project's local models -- every "
        "point sits at ASR=0.000. Bubble size (mean $/episode) is uniform because local "
        "(Ollama) inference is exactly $0 by construction, not because cost data is missing.",
        "",
        "| Defense | ASR@20 (n trials) | Utility (n episodes) | Mean $/episode | Frontier |",
        "| --- | --- | --- | --- | --- |",
    ]
    adaptive_frontier = {p.defense for p in pareto_frontier(adaptive_points)}
    for p in sorted(adaptive_points, key=lambda p: p.defense):
        marker = "**yes**" if p.defense in adaptive_frontier else "no"
        lines.append(
            f"| {p.defense} | {p.asr:.3f} ({p.n_asr}) | {p.utility:.3f} ({p.n_utility}) | "
            f"{p.mean_usd:.6f} | {marker} |"
        )

    lines += [
        "",
        "## Panel 2 -- static ASR, workspace suite, L5 (`openai/gpt-oss-120b`)",
        "",
        "The one place this project has ever measured real ASR variation: "
        "`configs/composition_groq_slice.yaml` (S6-03) plus `no_defense` (S2-11), "
        "`important_instructions`, n=3 episodes/cell. Utility is still the local-model rate "
        "(this slice never measured its own benign utility -- `docs/notes/release.md`). "
        "Bubble size is real and varies here: Groq is a priced API, unlike Ollama.",
        "",
        "| Defense | ASR (n episodes) | Utility (n episodes) | Mean $/episode | Frontier |",
        "| --- | --- | --- | --- | --- |",
    ]
    l5_frontier = {p.defense for p in pareto_frontier(l5_points)}
    for p in sorted(l5_points, key=lambda p: p.defense):
        marker = "**yes**" if p.defense in l5_frontier else "no"
        lines.append(
            f"| {p.defense} | {p.asr:.3f} ({p.n_asr}) | {p.utility:.3f} ({p.n_utility}) | "
            f"{p.mean_usd:.6f} | {marker} |"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    static_conn = connect(_STATIC_TRACE_DB)
    adaptive_conn = connect(_ADAPTIVE_TRACE_DB)
    try:
        adaptive_points = _adaptive_panel_points(static_conn, adaptive_conn)
        l5_points = _l5_panel_points(static_conn)
    finally:
        static_conn.close()
        adaptive_conn.close()

    _render_plot(adaptive_points, l5_points)
    markdown = _render_markdown(adaptive_points, l5_points)
    OUTPUT_MD_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_MD_PATH.write_text(markdown)
    print(
        f"wrote {OUTPUT_IMAGE_PATH}, {OUTPUT_MD_PATH} "
        f"({len(adaptive_points)} adaptive points, {len(l5_points)} L5 points)"
    )


if __name__ == "__main__":
    main()
