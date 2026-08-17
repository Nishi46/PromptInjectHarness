import sqlite3
from pathlib import Path

import pytest

from injection_pareto.trace import (
    cost_summary_by_episode,
    insert_cost_record,
    insert_defense_event,
    insert_episode,
    insert_run,
    insert_step,
    insert_tool_call,
    open_db,
    reconstruct_episode,
    transaction,
)


def _db_path(tmp_path: Path) -> Path:
    return tmp_path / "trace.db"


def test_full_episode_reconstructs_in_chronological_order(tmp_path: Path) -> None:
    with open_db(_db_path(tmp_path)) as conn:
        run_id = insert_run(
            conn,
            config_hash="abc",
            model="llama3.2:3b",
            defense_stack="no_defense",
            suite="workspace",
            started_at="t0",
        )
        episode_id = insert_episode(
            conn, run_id=run_id, task_id="user_task_0", started_at="t0", utility=True, security=True
        )

        with transaction(conn):
            step_0 = insert_step(
                conn,
                episode_id=episode_id,
                step_index=0,
                role="system",
                content="be helpful",
                timestamp="t0",
            )
            step_1 = insert_step(
                conn,
                episode_id=episode_id,
                step_index=1,
                role="user",
                content="do the thing",
                timestamp="t1",
            )
            step_2 = insert_step(
                conn,
                episode_id=episode_id,
                step_index=2,
                role="assistant",
                content="",
                timestamp="t2",
            )
            insert_tool_call(
                conn,
                step_id=step_2,
                tool_name="send_email",
                arguments_json='{"to": "a@b.com"}',
                result_json='"sent"',
                timestamp="t2",
            )
            insert_defense_event(
                conn,
                episode_id=episode_id,
                step_id=step_2,
                defense_name="no_defense",
                hook="on_pre_tool_call",
                verdict="allow",
                timestamp="t2",
            )
            insert_defense_event(
                conn,
                episode_id=episode_id,
                step_id=None,
                defense_name="no_defense",
                hook="on_pre_generate",
                verdict="allow",
                timestamp="t0",
            )

        trace = reconstruct_episode(conn, episode_id)

    assert [s.id for s in trace.steps] == [step_0, step_1, step_2]
    assert [s.step_index for s in trace.steps] == [0, 1, 2]
    assert trace.steps[2].tool_calls[0].tool_name == "send_email"
    assert trace.steps[2].defense_events[0].hook == "on_pre_tool_call"
    assert trace.episode_defense_events[0].hook == "on_pre_generate"


def test_foreign_key_constraint_rejects_orphan_step(tmp_path: Path) -> None:
    with open_db(_db_path(tmp_path)) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            insert_step(
                conn, episode_id=999999, step_index=0, role="user", content="orphan", timestamp="t0"
            )


def test_foreign_key_constraint_rejects_orphan_tool_call(tmp_path: Path) -> None:
    with open_db(_db_path(tmp_path)) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            insert_tool_call(
                conn,
                step_id=999999,
                tool_name="send_email",
                arguments_json="{}",
                timestamp="t0",
            )


def test_cost_and_latency_aggregation_matches_hand_computed_fixture(tmp_path: Path) -> None:
    with open_db(_db_path(tmp_path)) as conn:
        run_id = insert_run(
            conn,
            config_hash="abc",
            model="llama-3.3-70b-versatile",
            defense_stack="no_defense",
            suite="workspace",
            started_at="t0",
        )
        episode_id = insert_episode(conn, run_id=run_id, task_id="user_task_0", started_at="t0")

        # Fixture: 4 calls with known usd/wall_ms so the aggregation can be hand-checked.
        fixture = [
            (0.01, 100),
            (0.02, 300),
            (0.015, 500),
            (0.025, 900),
        ]
        with transaction(conn):
            for usd, wall_ms in fixture:
                insert_cost_record(
                    conn,
                    run_id=run_id,
                    episode_id=episode_id,
                    model="llama-3.3-70b-versatile",
                    tokens_in=100,
                    tokens_out=50,
                    wall_ms=wall_ms,
                    usd=usd,
                    timestamp="t0",
                )

        summary = cost_summary_by_episode(conn, run_id=run_id)

        # Also confirmed directly via SQL — the Sprint 1 acceptance criterion is
        # "a SQL query returns total $ ... per episode" (p95 has no SQLite
        # aggregate, hence the Python-side check below).
        row = conn.execute(
            "SELECT SUM(usd) AS total_usd FROM cost_record WHERE episode_id = ?", (episode_id,)
        ).fetchone()

    assert len(summary) == 1
    result = summary[0]
    expected_total_usd = sum(usd for usd, _ in fixture)
    assert result.total_usd == pytest.approx(expected_total_usd)
    assert row["total_usd"] == pytest.approx(expected_total_usd)

    # p95 by linear interpolation (numpy/statistics default) over [100, 300, 500, 900]:
    # rank = (4-1)*0.95 = 2.85 -> interpolate between sorted[2]=500 and sorted[3]=900
    expected_p95 = 500 + (900 - 500) * 0.85
    assert result.p95_wall_ms == pytest.approx(expected_p95)


def test_cache_hit_episode_records_zero_cost(tmp_path: Path) -> None:
    with open_db(_db_path(tmp_path)) as conn:
        run_id = insert_run(
            conn,
            config_hash="abc",
            model="llama3.2:3b",
            defense_stack="no_defense",
            suite="workspace",
            started_at="t0",
        )
        episode_id = insert_episode(conn, run_id=run_id, task_id="user_task_0", started_at="t0")

        with transaction(conn):
            # First call: a real (non-cached) generation with nonzero cost.
            insert_cost_record(
                conn,
                run_id=run_id,
                episode_id=episode_id,
                model="llama3.2:3b",
                tokens_in=100,
                tokens_out=50,
                wall_ms=800,
                usd=0.01,
                cache_hit=False,
                timestamp="t0",
            )
            # Second call: identical request served from cache (S1-05) — $0,
            # cache_hit=True, but tokens still recorded for reference.
            insert_cost_record(
                conn,
                run_id=run_id,
                episode_id=episode_id,
                model="llama3.2:3b",
                tokens_in=100,
                tokens_out=50,
                wall_ms=0,
                usd=0.0,
                cache_hit=True,
                timestamp="t1",
            )

        rows = conn.execute(
            "SELECT usd, cache_hit FROM cost_record WHERE episode_id = ? ORDER BY timestamp",
            (episode_id,),
        ).fetchall()

    assert rows[0]["cache_hit"] == 0
    assert rows[0]["usd"] == pytest.approx(0.01)
    assert rows[1]["cache_hit"] == 1
    assert rows[1]["usd"] == 0.0
