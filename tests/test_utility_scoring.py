import sqlite3
from pathlib import Path

import pytest

from injection_pareto.scoring import (
    InjectionFreeViolation,
    UtilityRow,
    assert_injection_free,
    benign_utility_rate,
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
