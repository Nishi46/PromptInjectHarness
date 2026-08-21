"""S6-03: unit tests for `scripts/generate_composition_results.py`'s
independence-formula computation, against fake `SecurityRow` fixtures --
not a live-model assertion, matching S5-02's own precedent for why (the
*actual* ASR is an empirical finding to report, not a spec to test
against). `scripts/` isn't an importable package (no other `generate_*.py`
script has tests, per every prior sprint), so this loads the module
directly from its file path -- the standard technique for unit-testing a
plain script, scoped to this one test file only."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "generate_composition_results.py"
_spec = importlib.util.spec_from_file_location("generate_composition_results", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
generate_composition_results = importlib.util.module_from_spec(_spec)
sys.modules["generate_composition_results"] = generate_composition_results
_spec.loader.exec_module(generate_composition_results)

SecurityRow = generate_composition_results.SecurityRow
_independence_table = generate_composition_results._independence_table
_COMPOSITE_PAIRS = generate_composition_results._COMPOSITE_PAIRS


def _security_row(
    defense: str, asr: float, *, n: int = 3, suite: str = "workspace", attack: str = "naive",
    model: str = "L1",
) -> SecurityRow:  # type: ignore[valid-type]
    return SecurityRow(
        suite=suite, defense=defense, attack=attack, model=model, n_episodes=n, asr=asr
    )


def test_predicted_asr_uses_the_union_of_independent_events_formula() -> None:
    """0.4 + 0.5 - 0.4*0.5 = 0.7, not 0.2 (the AND/product model) and not
    0.9 (naive addition) -- the exact formula the checklist mandates."""
    rows = [
        _security_row("spotlighting", 0.4),
        _security_row("instructional_prevention", 0.5),
        _security_row("spotlighting+instructional_prevention", 0.6),
    ]

    table = _independence_table(rows)
    row = next(r for r in table if r.pair == "spotlighting+instructional_prevention")

    assert row.predicted is not None
    assert row.predicted == 0.4 + 0.5 - 0.4 * 0.5
    assert row.predicted == 0.7


def test_delta_is_observed_minus_predicted() -> None:
    rows = [
        _security_row("spotlighting", 0.4),
        _security_row("instructional_prevention", 0.5),
        _security_row("spotlighting+instructional_prevention", 0.6),
    ]

    table = _independence_table(rows)
    row = next(r for r in table if r.pair == "spotlighting+instructional_prevention")

    assert row.observed == 0.6
    assert row.delta is not None
    assert abs(row.delta - (0.6 - 0.7)) < 1e-9


def test_missing_pair_data_gives_none_not_zero_for_observed_and_delta() -> None:
    """A missing measurement must never silently read as a confirmed
    `0.000` -- the checklist's own explicit requirement."""
    rows = [
        _security_row("spotlighting", 0.4),
        _security_row("instructional_prevention", 0.5),
        # No `spotlighting+instructional_prevention` row at all.
    ]

    table = _independence_table(rows)
    row = next(r for r in table if r.pair == "spotlighting+instructional_prevention")

    assert row.predicted == 0.4 + 0.5 - 0.4 * 0.5  # still computable from the two solo ASRs
    assert row.observed is None
    assert row.delta is None


def test_missing_solo_defense_gives_none_predicted() -> None:
    """If even one of the two components' ASR is unmeasured, `predicted`
    must be `None`, not silently computed with the missing side treated
    as 0.0 (which would fabricate a prediction that was never actually
    derivable)."""
    rows = [
        _security_row("spotlighting", 0.4),
        # No `instructional_prevention` row at all.
        _security_row("spotlighting+instructional_prevention", 0.1),
    ]

    table = _independence_table(rows)
    row = next(r for r in table if r.pair == "spotlighting+instructional_prevention")

    assert row.asr_a == 0.4
    assert row.asr_b is None
    assert row.predicted is None
    assert row.delta is None  # can't compute a delta against a missing prediction either


def test_zero_asr_on_both_sides_predicts_zero() -> None:
    rows = [
        _security_row("instructional_prevention", 0.0),
        _security_row("tool_allowlist", 0.0),
        _security_row("instructional_prevention+tool_allowlist", 0.0),
    ]

    table = _independence_table(rows)
    row = next(r for r in table if r.pair == "instructional_prevention+tool_allowlist")

    assert row.predicted == 0.0
    assert row.observed == 0.0
    assert row.delta == 0.0


def test_both_orderings_of_a_pair_get_the_same_predicted_value() -> None:
    """The independence formula is symmetric in A and B -- `A+B` and
    `B+A` must predict identically, even though their *observed* values
    (if measured) can differ."""
    rows = [
        _security_row("spotlighting", 0.3),
        _security_row("guard_model", 0.6),
        _security_row("spotlighting+guard_model", 0.2),
        _security_row("guard_model+spotlighting", 0.9),
    ]

    table = _independence_table(rows)
    forward = next(r for r in table if r.pair == "spotlighting+guard_model")
    backward = next(r for r in table if r.pair == "guard_model+spotlighting")

    assert forward.predicted == backward.predicted
    assert forward.observed != backward.observed


def test_no_rows_at_all_for_a_population_are_silently_skipped() -> None:
    """A (suite, attack, model) population with zero data for a given pair
    (neither solo defense nor the composite itself) produces no row for
    it -- not a spurious all-`None` row cluttering the output."""
    rows = [_security_row("spotlighting", 0.4, attack="naive")]

    table = _independence_table(rows)

    assert all(r.pair != "instructional_prevention+tool_allowlist" for r in table)


def test_covers_every_declared_composite_pair_when_all_data_present() -> None:
    solo_defenses = ("spotlighting", "instructional_prevention", "guard_model", "tool_allowlist")
    rows = [_security_row(name, 0.1) for name in solo_defenses]
    rows += [_security_row(pair, 0.1) for pair in _COMPOSITE_PAIRS]

    table = _independence_table(rows)

    assert {r.pair for r in table} == set(_COMPOSITE_PAIRS)
