import sqlite3
from pathlib import Path

import pytest

from injection_pareto.trace import (
    insert_adaptive_round,
    insert_adaptive_trial,
    insert_episode,
    insert_run,
    open_db,
    update_adaptive_trial_result,
)


def _db_path(tmp_path: Path) -> Path:
    return tmp_path / "trace.db"


def _run_id(conn: sqlite3.Connection) -> int:
    return insert_run(
        conn,
        config_hash="abc",
        model="llama3.2:3b",
        defense_stack="no_defense",
        suite="workspace",
        started_at="t0",
    )


def test_adaptive_tables_exist_after_init_db(tmp_path: Path) -> None:
    with open_db(_db_path(tmp_path)) as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert {"adaptive_trial", "adaptive_round"} <= tables


def test_trial_and_rounds_round_trip(tmp_path: Path) -> None:
    with open_db(_db_path(tmp_path)) as conn:
        run_id = _run_id(conn)
        trial_id = insert_adaptive_trial(
            conn,
            run_id=run_id,
            task_id="user_task_0",
            defense="no_defense",
            attack_family="naive",
            suite="workspace",
            budget=20,
            started_at="t0",
        )

        episode_1 = insert_episode(
            conn,
            run_id=run_id,
            task_id="user_task_0",
            injection_task_id="injection_task_0",
            started_at="t0",
            security=False,
        )
        episode_2 = insert_episode(
            conn,
            run_id=run_id,
            task_id="user_task_0",
            injection_task_id="injection_task_0",
            started_at="t1",
            security=True,
        )

        round_1 = insert_adaptive_round(
            conn,
            trial_id=trial_id,
            round_index=1,
            episode_id=episode_1,
            payload_text="TODO: do the thing",
            timestamp="t0",
        )
        round_2 = insert_adaptive_round(
            conn,
            trial_id=trial_id,
            round_index=2,
            episode_id=episode_2,
            payload_text="TODO: do the thing (mutated)",
            timestamp="t1",
        )
        conn.commit()

        update_adaptive_trial_result(
            conn, trial_id=trial_id, success=True, rounds_to_success=2, ended_at="t1"
        )

        trial_row = conn.execute(
            "SELECT budget, success, rounds_to_success, ended_at FROM adaptive_trial WHERE id = ?",
            (trial_id,),
        ).fetchone()
        round_rows = conn.execute(
            "SELECT id, round_index, episode_id, payload_text FROM adaptive_round "
            "WHERE trial_id = ? ORDER BY round_index",
            (trial_id,),
        ).fetchall()

    assert (trial_row["budget"], trial_row["success"], trial_row["rounds_to_success"]) == (
        20,
        1,
        2,
    )
    assert trial_row["ended_at"] == "t1"
    assert [r["id"] for r in round_rows] == [round_1, round_2]
    assert [r["round_index"] for r in round_rows] == [1, 2]
    assert [r["episode_id"] for r in round_rows] == [episode_1, episode_2]
    assert round_rows[1]["payload_text"] == "TODO: do the thing (mutated)"


def test_adaptive_round_rejects_orphan_trial_id(tmp_path: Path) -> None:
    with open_db(_db_path(tmp_path)) as conn:
        run_id = _run_id(conn)
        episode_id = insert_episode(
            conn,
            run_id=run_id,
            task_id="user_task_0",
            injection_task_id="injection_task_0",
            started_at="t0",
        )
        with pytest.raises(sqlite3.IntegrityError):
            insert_adaptive_round(
                conn,
                trial_id=999999,
                round_index=1,
                episode_id=episode_id,
                payload_text="TODO: do the thing",
                timestamp="t0",
            )


def test_adaptive_round_rejects_orphan_episode_id(tmp_path: Path) -> None:
    with open_db(_db_path(tmp_path)) as conn:
        run_id = _run_id(conn)
        trial_id = insert_adaptive_trial(
            conn,
            run_id=run_id,
            task_id="user_task_0",
            defense="no_defense",
            attack_family="naive",
            suite="workspace",
            budget=20,
            started_at="t0",
        )
        with pytest.raises(sqlite3.IntegrityError):
            insert_adaptive_round(
                conn,
                trial_id=trial_id,
                round_index=1,
                episode_id=999999,
                payload_text="TODO: do the thing",
                timestamp="t0",
            )
