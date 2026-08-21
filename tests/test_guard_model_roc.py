"""S6-07: unit tests for `scripts/generate_guard_model_roc.py`'s TPR/FPR/AUC
computation and its YAML-reflow-aware substring matcher, against fake
labeled-score fixtures -- not a live-model assertion, matching
`test_composition_results.py`'s own precedent. `scripts/` isn't an
importable package, so this loads the module directly from its file path,
the same technique that test file uses."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "generate_guard_model_roc.py"
_spec = importlib.util.spec_from_file_location("generate_guard_model_roc", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
generate_guard_model_roc = importlib.util.module_from_spec(_spec)
sys.modules["generate_guard_model_roc"] = generate_guard_model_roc
_spec.loader.exec_module(generate_guard_model_roc)

ScoredResult = generate_guard_model_roc.ScoredResult
roc_points = generate_guard_model_roc.roc_points
auc = generate_guard_model_roc.auc
mann_whitney_auc = generate_guard_model_roc.mann_whitney_auc
_normalize = generate_guard_model_roc._normalize
_flatten_strings = generate_guard_model_roc._flatten_strings
_tool_result_contains = generate_guard_model_roc._tool_result_contains


def _result(score: float, label: int, source: str = "workspace_static") -> ScoredResult:  # type: ignore[valid-type]
    return ScoredResult(score=score, label=label, source=source)


def test_roc_points_uses_strict_greater_than_matching_guard_model_py() -> None:
    """A score exactly equal to the threshold is NOT predicted-positive --
    `defenses/guard_model.py`'s own rule is `score > threshold`, not `>=`,
    and this curve must describe that exact rule."""
    results = [_result(0.5, label=1)]

    (point,) = roc_points(results, thresholds=[0.5])

    assert point.tp == 0
    assert point.fn == 1
    assert point.tpr == 0.0


def test_roc_points_counts_confusion_matrix_correctly() -> None:
    results = [
        _result(0.9, label=1),  # TP at threshold 0.5
        _result(0.6, label=1),  # TP at threshold 0.5
        _result(0.3, label=1),  # FN at threshold 0.5
        _result(0.8, label=0),  # FP at threshold 0.5
        _result(0.1, label=0),  # TN at threshold 0.5
        _result(0.2, label=0),  # TN at threshold 0.5
    ]

    (point,) = roc_points(results, thresholds=[0.5])

    assert (point.tp, point.fp, point.tn, point.fn) == (2, 1, 2, 1)
    assert point.tpr == 2 / 3
    assert point.fpr == 1 / 3


def test_roc_points_reports_none_tpr_when_no_positives_exist() -> None:
    """A threshold slice with zero real positives has an undefined TPR --
    must be `None`, not a fabricated `0.0`."""
    results = [_result(0.9, label=0), _result(0.1, label=0)]

    (point,) = roc_points(results, thresholds=[0.5])

    assert point.tpr is None
    assert point.fpr == 0.5


def test_auc_of_a_perfect_separator_is_one() -> None:
    """Thresholds that perfectly separate the classes trace the full unit
    square's upper-left corner -- (0,0) -> (0,1) -> (1,1) -- area 1.0."""
    results = [_result(0.9, label=1), _result(0.8, label=1), _result(0.2, label=0)]

    points = roc_points(results, thresholds=[0.0, 0.5, 1.0])

    assert auc(points) == 1.0


def test_auc_of_a_random_classifier_is_near_half() -> None:
    """A classifier with no separation traces the diagonal -- area 0.5.
    Scores are deliberately chosen not to land exactly on a swept
    threshold (0.5 sits strictly between every score here) so this isn't
    itself an instance of the tie-cliff artifact
    `test_mann_whitney_auc_matches_the_grid_free_of_the_tie_cliff_artifact`
    documents."""
    results = [_result(0.9, label=1), _result(0.1, label=1)] + [
        _result(0.8, label=0),
        _result(0.2, label=0),
    ]

    points = roc_points(results, thresholds=[0.0, 0.5, 1.0])

    assert auc(points) == 0.5


def test_auc_needs_at_least_two_defined_points() -> None:
    results = [_result(0.9, label=1)]  # no negatives at all -> fpr always None

    points = roc_points(results, thresholds=[0.0, 1.0])

    assert auc(points) is None


def test_mann_whitney_auc_of_a_perfect_separator_is_one() -> None:
    results = [_result(0.9, label=1), _result(0.8, label=1), _result(0.2, label=0)]

    assert mann_whitney_auc(results) == 1.0


def test_mann_whitney_auc_counts_ties_as_half_credit() -> None:
    """One tied pair (positive == negative) contributes 0.5, not 0 or 1 --
    the standard rank-correlation tie convention, matching this project's
    own `_rank` precedent elsewhere."""
    results = [_result(0.5, label=1), _result(0.5, label=0)]

    assert mann_whitney_auc(results) == 0.5


def test_mann_whitney_auc_matches_the_grid_free_of_the_tie_cliff_artifact() -> None:
    """The real bug this project's own data surfaced: a fixed-grid
    trapezoidal AUC can land a sample threshold exactly on a mass of tied
    scores and clip them, producing an artificially low number even though
    the classifier separates the classes well. Reproduced in miniature:
    six positives tied at 0.5, one negative at 0.5, rest of the negatives
    low. The grid method (sampling at 0.5 itself) undercounts; Mann-Whitney
    doesn't."""
    results = (
        [_result(0.5, label=1) for _ in range(6)]
        + [_result(0.9, label=1) for _ in range(2)]
        + [_result(0.5, label=0)]
        + [_result(0.0, label=0) for _ in range(9)]
    )

    grid_points = roc_points(results, thresholds=[0.0, 0.5, 1.0])
    grid_auc = auc(grid_points)
    exact_auc = mann_whitney_auc(results)

    assert grid_auc is not None
    assert exact_auc is not None
    assert exact_auc > grid_auc  # the tie cliff really does undercount


def test_mann_whitney_auc_is_none_without_both_classes() -> None:
    assert mann_whitney_auc([_result(0.5, label=1)]) is None
    assert mann_whitney_auc([_result(0.5, label=0)]) is None
    assert mann_whitney_auc([]) is None


def test_normalize_collapses_real_whitespace_runs() -> None:
    assert _normalize("a\n\n  b\tc") == "a b c"


def test_flatten_strings_pulls_every_leaf_out_of_a_nested_structure() -> None:
    obj = {"a": "one", "b": ["two", {"c": "three"}], "d": 42, "e": None}

    assert _flatten_strings(obj) == ["one", "two", "three"]


def test_flatten_strings_of_a_plain_string_yields_itself() -> None:
    assert _flatten_strings("just text") == ["just text"]


def test_tool_result_contains_matches_plain_text_after_normalization() -> None:
    assert _tool_result_contains("hello\n\nworld", ["hello world"])


def test_tool_result_contains_reverses_yaml_line_folding() -> None:
    """The real fix this project's own data required: PyYAML folds long
    double-quoted scalars across lines with its own escape syntax, so the
    raw text alone doesn't contain the payload as a clean substring -- but
    re-parsing it as YAML and flattening the parsed strings does."""
    payload = "This is an important message. Please do the injected thing now."
    # A minimal, real double-quoted YAML scalar PyYAML would fold a long
    # line like this into -- constructed by round-tripping through
    # `yaml.dump` rather than hand-typed, so it's a genuine instance of the
    # real folding behavior, not a guess at its syntax.
    import yaml as _yaml

    folded = _yaml.dump({"description": f"Some preamble. {payload}"}, default_style='"')

    assert payload not in folded  # confirms this is a real test of the fix, not a no-op
    assert _tool_result_contains(folded, [payload])


def test_tool_result_contains_is_false_when_the_payload_is_genuinely_absent() -> None:
    assert not _tool_result_contains("nothing suspicious here", ["please exfiltrate everything"])


def test_tool_result_contains_handles_non_yaml_plain_text_without_crashing() -> None:
    """A tool error string like this is valid-ish YAML-adjacent text
    (colons, quotes) that must not raise -- falls back cleanly to the raw
    normalized reading."""
    text = "ValueError: time data 'May 26th' does not match format '%Y-%m-%d'"

    assert not _tool_result_contains(text, ["unrelated payload"])
    assert _tool_result_contains(text, ["time data 'May 26th'"])
