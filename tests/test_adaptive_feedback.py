import sqlite3
from pathlib import Path

import pytest

from injection_pareto.adaptive import extract_feedback
from injection_pareto.trace import (
    insert_defense_event,
    insert_episode,
    insert_run,
    insert_step,
    open_db,
    transaction,
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


def test_full_compromise_reports_success(tmp_path: Path) -> None:
    with open_db(_db_path(tmp_path)) as conn:
        run_id = _run_id(conn)
        episode_id = insert_episode(
            conn,
            run_id=run_id,
            task_id="user_task_0",
            injection_task_id="injection_task_0",
            started_at="t0",
            security=True,
        )

        feedback = extract_feedback(conn, episode_id=episode_id)

    assert feedback.success is True
    assert feedback.defense_intervened is False
    assert feedback.refusal_text is None


def test_blocked_tool_call_reports_defense_intervened(tmp_path: Path) -> None:
    with open_db(_db_path(tmp_path)) as conn:
        run_id = _run_id(conn)
        episode_id = insert_episode(
            conn,
            run_id=run_id,
            task_id="user_task_0",
            injection_task_id="injection_task_0",
            started_at="t0",
            security=False,
        )

        with transaction(conn):
            step_id = insert_step(
                conn,
                episode_id=episode_id,
                step_index=0,
                role="assistant",
                content="",
                timestamp="t0",
            )
            insert_defense_event(
                conn,
                episode_id=episode_id,
                step_id=step_id,
                defense_name="tool_allowlist",
                hook="on_pre_tool_call",
                verdict="block",
                timestamp="t0",
            )

        feedback = extract_feedback(conn, episode_id=episode_id)

    assert feedback.success is False
    assert feedback.defense_intervened is True


def test_episode_level_block_also_reports_defense_intervened(tmp_path: Path) -> None:
    with open_db(_db_path(tmp_path)) as conn:
        run_id = _run_id(conn)
        episode_id = insert_episode(
            conn,
            run_id=run_id,
            task_id="user_task_0",
            injection_task_id="injection_task_0",
            started_at="t0",
            security=False,
        )

        with transaction(conn):
            insert_defense_event(
                conn,
                episode_id=episode_id,
                step_id=None,
                defense_name="spotlighting",
                hook="on_pre_generate",
                verdict="block",
                timestamp="t0",
            )

        feedback = extract_feedback(conn, episode_id=episode_id)

    assert feedback.defense_intervened is True


def test_refusal_text_is_last_assistant_step_truncated_at_cap(tmp_path: Path) -> None:
    with open_db(_db_path(tmp_path)) as conn:
        run_id = _run_id(conn)
        episode_id = insert_episode(
            conn,
            run_id=run_id,
            task_id="user_task_0",
            injection_task_id="injection_task_0",
            started_at="t0",
            security=False,
        )

        long_text = "x" * 600
        with transaction(conn):
            insert_step(
                conn,
                episode_id=episode_id,
                step_index=0,
                role="assistant",
                content="I can't help with that.",
                timestamp="t0",
            )
            insert_step(
                conn,
                episode_id=episode_id,
                step_index=1,
                role="assistant",
                content=long_text,
                timestamp="t1",
            )

        feedback = extract_feedback(conn, episode_id=episode_id)

    assert feedback.refusal_text == long_text[:500]
    assert len(feedback.refusal_text) == 500


def test_no_assistant_steps_leaves_refusal_text_none(tmp_path: Path) -> None:
    with open_db(_db_path(tmp_path)) as conn:
        run_id = _run_id(conn)
        episode_id = insert_episode(
            conn,
            run_id=run_id,
            task_id="user_task_0",
            injection_task_id="injection_task_0",
            started_at="t0",
            security=False,
        )

        with transaction(conn):
            insert_step(
                conn,
                episode_id=episode_id,
                step_index=0,
                role="user",
                content="do the thing",
                timestamp="t0",
            )

        feedback = extract_feedback(conn, episode_id=episode_id)

    assert feedback.refusal_text is None


def test_empty_assistant_content_is_skipped(tmp_path: Path) -> None:
    with open_db(_db_path(tmp_path)) as conn:
        run_id = _run_id(conn)
        episode_id = insert_episode(
            conn,
            run_id=run_id,
            task_id="user_task_0",
            injection_task_id="injection_task_0",
            started_at="t0",
            security=False,
        )

        with transaction(conn):
            insert_step(
                conn,
                episode_id=episode_id,
                step_index=0,
                role="assistant",
                content="here's my answer",
                timestamp="t0",
            )
            insert_step(
                conn,
                episode_id=episode_id,
                step_index=1,
                role="assistant",
                content="",
                timestamp="t1",
            )

        feedback = extract_feedback(conn, episode_id=episode_id)

    assert feedback.refusal_text == "here's my answer"


def test_benign_episode_raises(tmp_path: Path) -> None:
    with open_db(_db_path(tmp_path)) as conn:
        run_id = _run_id(conn)
        episode_id = insert_episode(
            conn, run_id=run_id, task_id="user_task_0", started_at="t0", utility=True
        )

        with pytest.raises(ValueError, match="benign"):
            extract_feedback(conn, episode_id=episode_id)


def test_unknown_episode_raises(tmp_path: Path) -> None:
    with open_db(_db_path(tmp_path)) as conn:
        with pytest.raises(ValueError, match="no episode"):
            extract_feedback(conn, episode_id=999999)
