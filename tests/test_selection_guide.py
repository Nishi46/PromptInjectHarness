"""S7-02: unit tests for `scripts/generate_selection_guide.py`'s
`_combine_guide_rows` -- the pure combination step, tested against fake
fetched-data fixtures rather than a live trace DB, matching
`test_composition_results.py`'s `_independence_table` precedent (query
functions untested, the pure combiner is). `scripts/` isn't an importable
package, so this loads the module directly from its file path, the same
technique that test file uses."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "generate_selection_guide.py"
_spec = importlib.util.spec_from_file_location("generate_selection_guide", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
generate_selection_guide = importlib.util.module_from_spec(_spec)
sys.modules["generate_selection_guide"] = generate_selection_guide
_spec.loader.exec_module(generate_selection_guide)

_combine_guide_rows = generate_selection_guide._combine_guide_rows
_DEFENSES = generate_selection_guide._DEFENSES


def test_covers_every_declared_defense_even_with_no_data_at_all() -> None:
    rows = _combine_guide_rows(local_asr={}, l5_asr={}, utility={}, overhead={})

    assert {r.defense for r in rows} == {name for name, _ in _DEFENSES}
    assert all(r.local_asr is None for r in rows)
    assert all(r.l5_asr is None for r in rows)
    assert all(r.utility_tax is None for r in rows)


def test_missing_l5_data_is_none_not_a_fabricated_zero() -> None:
    rows = _combine_guide_rows(
        local_asr={"no_defense": (0.0, 3)},
        l5_asr={},  # never run on L5
        utility={"no_defense": (0.333, 3)},
        overhead={},
    )
    row = next(r for r in rows if r.defense == "no_defense")

    assert row.l5_asr is None
    assert row.n_l5_asr == 0


def test_utility_tax_sign_matches_scoring_utility_tax_row_convention() -> None:
    """`tax = no_defense_rate - defense_rate` -- positive means the defense
    completes *fewer* benign tasks than the baseline (a real tax);
    negative means it did better. Matches
    `scoring.utility.UtilityTaxRow`'s own documented convention exactly."""
    rows = _combine_guide_rows(
        local_asr={},
        l5_asr={},
        utility={"no_defense": (0.5, 6), "spotlighting": (0.3, 6)},
        overhead={},
    )
    row = next(r for r in rows if r.defense == "spotlighting")

    assert row.utility_tax is not None
    assert abs(row.utility_tax - 0.2) < 1e-9  # a real tax: worse than baseline


def test_utility_tax_can_be_negative_when_the_defense_does_better() -> None:
    rows = _combine_guide_rows(
        local_asr={},
        l5_asr={},
        utility={"no_defense": (0.3, 6), "instructional_prevention": (0.5, 6)},
        overhead={},
    )
    row = next(r for r in rows if r.defense == "instructional_prevention")

    assert row.utility_tax is not None
    assert row.utility_tax < 0


def test_utility_tax_is_none_when_baseline_utility_is_unmeasured() -> None:
    """No `no_defense` utility at all -- every tax must be `None`, not
    silently computed against a missing baseline."""
    rows = _combine_guide_rows(
        local_asr={},
        l5_asr={},
        utility={"spotlighting": (0.5, 6)},
        overhead={},
    )
    row = next(r for r in rows if r.defense == "spotlighting")

    assert row.utility_tax is None


def test_defense_with_no_overhead_rows_gets_a_structural_zero_not_missing() -> None:
    """A defense absent from `overhead` made zero extra calls -- reported
    as an exact `0.0`/`0.0`, not `None` (it's a real, structural fact:
    prompt/regex-only defenses never write a `defense:` cost_record row)."""
    rows = _combine_guide_rows(local_asr={}, l5_asr={}, utility={}, overhead={})
    row = next(r for r in rows if r.defense == "capability_enforcement")

    assert row.mean_defense_usd == 0.0
    assert row.mean_defense_ms == 0.0


def test_defense_with_real_overhead_reports_it() -> None:
    rows = _combine_guide_rows(
        local_asr={},
        l5_asr={},
        utility={},
        overhead={"guard_model": (0.0, 959.0)},
    )
    row = next(r for r in rows if r.defense == "guard_model")

    assert row.mean_defense_usd == 0.0
    assert row.mean_defense_ms == 959.0
