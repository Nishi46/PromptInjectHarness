"""S7-01: unit tests for `scripts/generate_pareto_plot.py`'s
`pareto_frontier()` computation, against fake `ParetoPoint` fixtures -- not
a live-model assertion, matching `test_composition_results.py`'s own
precedent. `scripts/` isn't an importable package, so this loads the
module directly from its file path, the same technique that test file
uses."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "generate_pareto_plot.py"
_spec = importlib.util.spec_from_file_location("generate_pareto_plot", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
generate_pareto_plot = importlib.util.module_from_spec(_spec)
sys.modules["generate_pareto_plot"] = generate_pareto_plot
_spec.loader.exec_module(generate_pareto_plot)

ParetoPoint = generate_pareto_plot.ParetoPoint
pareto_frontier = generate_pareto_plot.pareto_frontier


def _point(defense: str, asr: float, utility: float, usd: float = 0.0) -> ParetoPoint:  # type: ignore[valid-type]
    return ParetoPoint(
        defense=defense, asr=asr, n_asr=3, utility=utility, n_utility=3, mean_usd=usd
    )


def test_a_strictly_dominated_point_is_excluded() -> None:
    """`b` has both higher ASR and lower utility than `a` -- strictly
    worse on both axes, must not be on the frontier."""
    a = _point("a", asr=0.1, utility=0.9)
    b = _point("b", asr=0.5, utility=0.5)

    frontier = pareto_frontier([a, b])

    assert frontier == [a]


def test_a_point_better_on_only_one_axis_is_not_dominated() -> None:
    """`a` has lower ASR but `b` has higher utility -- neither dominates
    the other, both belong on the frontier."""
    a = _point("a", asr=0.1, utility=0.5)
    b = _point("b", asr=0.3, utility=0.9)

    frontier = pareto_frontier([a, b])

    assert {p.defense for p in frontier} == {"a", "b"}


def test_exact_ties_are_both_kept_not_treated_as_mutually_dominating() -> None:
    """Two points with identical (asr, utility) don't dominate each other
    -- an identical twin isn't *strictly* better on any axis."""
    a = _point("a", asr=0.2, utility=0.6)
    b = _point("b", asr=0.2, utility=0.6)

    frontier = pareto_frontier([a, b])

    assert {p.defense for p in frontier} == {"a", "b"}


def test_a_point_tied_on_one_axis_and_worse_on_the_other_is_dominated() -> None:
    """Same utility as `a`, but strictly higher ASR -- `b` is dominated
    (not-worse-and-strictly-better-on-at-least-one-axis is satisfied)."""
    a = _point("a", asr=0.1, utility=0.5)
    b = _point("b", asr=0.4, utility=0.5)

    frontier = pareto_frontier([a, b])

    assert frontier == [a]


def test_single_point_is_always_on_the_frontier() -> None:
    a = _point("a", asr=0.5, utility=0.5)

    assert pareto_frontier([a]) == [a]


def test_empty_input_gives_empty_frontier() -> None:
    assert pareto_frontier([]) == []


def test_this_projects_real_panel_1_shape() -> None:
    """Pinned to the real S7-01 adaptive-panel data
    (`results/pareto.md`): every point tied at ASR=0, only the
    highest-utility group (0.667) survives -- `instructional_prevention`
    and `canary`, both real, both kept (exact tie, per the test above)."""
    points = [
        _point("canary", 0.0, 0.667),
        _point("guard_model", 0.0, 0.333),
        _point("instructional_prevention", 0.0, 0.667),
        _point("no_defense", 0.0, 0.333),
        _point("spotlighting", 0.0, 0.5),
        _point("tool_allowlist", 0.0, 0.333),
    ]

    frontier = pareto_frontier(points)

    assert {p.defense for p in frontier} == {"canary", "instructional_prevention"}
