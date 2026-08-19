import sqlite3
from pathlib import Path

import pytest

from injection_pareto.scoring import (
    InjectionFreeViolation,
    UtilityRate,
    UtilityRow,
    assert_injection_free,
    benign_utility_rate,
    utility_tax_table,
)
from injection_pareto.trace import insert_episode, insert_run, open_db


def _db_path(tmp_path: Path) -> Path:
    return tmp_path / "trace.db"


def _make_run(
    conn: sqlite3.Connection, *, model: str = "llama3.2:3b", defense: str = "no_defense"
) -> int:
    return insert_run(
        conn,
        config_hash="x",
        model=model,
        defense_stack=defense,
        suite="workspace",
        started_at="t0",
    )


def test_assert_injection_free_raises_on_an_attacked_episode() -> None:
    """The checklist's own scenario: feeding the assertion a fixture that
    includes an attacked episode must actively fail it, not silently pass."""
    rows = [
        UtilityRow(
            episode_id=1, injection_task_id=None, utility=True, defense="no_defense", model="m"
        ),
        UtilityRow(
            episode_id=2,
            injection_task_id="injection_task_0",
            utility=True,
            defense="no_defense",
            model="m",
        ),
    ]

    with pytest.raises(InjectionFreeViolation, match="episode.*2"):
        assert_injection_free(rows)


def test_assert_injection_free_passes_on_an_all_benign_fixture() -> None:
    rows = [
        UtilityRow(
            episode_id=1, injection_task_id=None, utility=True, defense="no_defense", model="m"
        ),
        UtilityRow(
            episode_id=2, injection_task_id=None, utility=False, defense="no_defense", model="m"
        ),
    ]

    assert_injection_free(rows)  # must not raise


def test_benign_utility_rate_excludes_attacked_episodes_from_the_computation(
    tmp_path: Path,
) -> None:
    """A run with both benign and attacked episodes (the normal case, per
    S1-08's own reproduction run) must compute the rate from the benign ones
    only -- an attacked episode's utility must never pull the number either
    way."""
    with open_db(_db_path(tmp_path)) as conn:
        run_id = _make_run(conn)
        insert_episode(conn, run_id=run_id, task_id="t0", started_at="t0", utility=True)
        insert_episode(conn, run_id=run_id, task_id="t1", started_at="t0", utility=False)
        # Attacked episode with utility=True -- if this leaked into the
        # computation the rate would be wrong (3/3 instead of 1/2).
        insert_episode(
            conn,
            run_id=run_id,
            task_id="t0",
            injection_task_id="injection_task_0",
            started_at="t0",
            utility=True,
        )

        rates = benign_utility_rate(conn, run_id=run_id)

    assert len(rates) == 1
    assert rates[0].n_episodes == 2
    assert rates[0].n_success == 1
    assert rates[0].rate == 0.5


def test_null_utility_episodes_are_excluded_not_counted_as_failure(tmp_path: Path) -> None:
    with open_db(_db_path(tmp_path)) as conn:
        run_id = _make_run(conn)
        insert_episode(conn, run_id=run_id, task_id="t0", started_at="t0", utility=True)
        insert_episode(conn, run_id=run_id, task_id="t1", started_at="t0", utility=None)

        rates = benign_utility_rate(conn, run_id=run_id)

    assert rates[0].n_episodes == 1
    assert rates[0].n_success == 1
    assert rates[0].rate == 1.0


def test_grouped_separately_per_defense_and_model(tmp_path: Path) -> None:
    with open_db(_db_path(tmp_path)) as conn:
        run_a = _make_run(conn, model="llama3.2:3b", defense="no_defense")
        run_b = _make_run(conn, model="llama3.2:3b", defense="spotlighting")
        insert_episode(conn, run_id=run_a, task_id="t0", started_at="t0", utility=True)
        insert_episode(conn, run_id=run_b, task_id="t0", started_at="t0", utility=False)

        rates = {(r.defense, r.model): r for r in benign_utility_rate(conn)}

    assert rates[("no_defense", "llama3.2:3b")].rate == 1.0
    assert rates[("spotlighting", "llama3.2:3b")].rate == 0.0


def test_run_id_scopes_the_computation(tmp_path: Path) -> None:
    with open_db(_db_path(tmp_path)) as conn:
        run_a = _make_run(conn)
        run_b = _make_run(conn)
        insert_episode(conn, run_id=run_a, task_id="t0", started_at="t0", utility=True)
        insert_episode(conn, run_id=run_b, task_id="t0", started_at="t0", utility=False)

        rates = benign_utility_rate(conn, run_id=run_a)

    assert len(rates) == 1
    assert rates[0].n_episodes == 1
    assert rates[0].rate == 1.0


def test_utility_tax_table_computes_a_positive_tax_when_target_does_worse(
    tmp_path: Path,
) -> None:
    """S5-02's headline scenario: `dual_llm` completes fewer benign tasks
    than `no_defense` on the same model -- a real utility tax, reported as
    a positive number (`baseline_rate - target_rate`)."""
    with open_db(_db_path(tmp_path)) as conn:
        baseline_run = _make_run(conn, model="llama3.2:3b", defense="no_defense")
        target_run = _make_run(conn, model="llama3.2:3b", defense="dual_llm")
        insert_episode(conn, run_id=baseline_run, task_id="t0", started_at="t0", utility=True)
        insert_episode(conn, run_id=baseline_run, task_id="t1", started_at="t0", utility=True)
        insert_episode(conn, run_id=target_run, task_id="t0", started_at="t0", utility=True)
        insert_episode(conn, run_id=target_run, task_id="t1", started_at="t0", utility=False)

        rates = benign_utility_rate(conn)

    rows = utility_tax_table(rates, baseline="no_defense", target="dual_llm")

    assert len(rows) == 1
    row = rows[0]
    assert row.model == "llama3.2:3b"
    assert row.baseline_rate == 1.0
    assert row.baseline_n == 2
    assert row.target_rate == 0.5
    assert row.target_n == 2
    assert row.tax == pytest.approx(0.5)


def test_utility_tax_table_reports_a_negative_tax_when_target_does_better() -> None:
    """The honest opposite case (S5-02's own inspected result on the real
    sweep, in fact): `dual_llm` completing *more* tasks than `no_defense`
    must not be clamped to 0 -- report the negative number plainly."""
    rates = [
        UtilityRate(defense="no_defense", model="m", n_episodes=2, n_success=1, rate=0.5),
        UtilityRate(defense="dual_llm", model="m", n_episodes=2, n_success=2, rate=1.0),
    ]

    rows = utility_tax_table(rates, baseline="no_defense", target="dual_llm")

    assert rows[0].tax == pytest.approx(-0.5)


def test_utility_tax_table_leaves_tax_none_when_a_model_is_missing_one_side() -> None:
    """A model that only ever ran under `no_defense` (never `dual_llm`, or
    vice versa) has nothing to subtract -- `tax` must be `None`, not a
    fabricated 0.0 that would misreport "no tax" for a comparison that was
    never actually measured."""
    rates = [
        UtilityRate(
            defense="no_defense", model="only_baseline", n_episodes=3, n_success=3, rate=1.0
        ),
        UtilityRate(defense="dual_llm", model="only_target", n_episodes=3, n_success=3, rate=1.0),
    ]

    tax_rows = utility_tax_table(rates, baseline="no_defense", target="dual_llm")
    rows = {row.model: row for row in tax_rows}

    assert rows["only_baseline"].target_rate is None
    assert rows["only_baseline"].tax is None
    assert rows["only_target"].baseline_rate is None
    assert rows["only_target"].tax is None
