from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL lets readers (e.g. a live results query) run without blocking on
    # an in-progress write, and lets concurrent sweep workers (S2-10) write
    # without serializing on the default rollback journal.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Idempotently create every trace table (and its indices) if missing."""
    conn.executescript(_SCHEMA_PATH.read_text())
    conn.commit()


@contextmanager
def open_db(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Connect, ensure the schema exists, and close on exit."""
    conn = connect(path)
    try:
        init_db(conn)
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Batch a group of inserts (e.g. one episode's steps/tool_calls/events)
    into a single commit instead of one fsync per row. `insert_step`,
    `insert_tool_call`, `insert_defense_event`, and `insert_cost_record` are
    the high-frequency writes and rely on the caller to wrap them in this;
    `insert_run` and `insert_episode` fire once per run/episode and commit
    on their own."""
    try:
        yield
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def insert_run(
    conn: sqlite3.Connection,
    *,
    config_hash: str,
    model: str,
    defense_stack: str,
    suite: str,
    started_at: str,
    attack: str | None = None,
    ended_at: str | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO run (config_hash, model, defense_stack, suite, attack, started_at, ended_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (config_hash, model, defense_stack, suite, attack, started_at, ended_at),
    )
    conn.commit()
    return int(cursor.lastrowid)  # type: ignore[arg-type]


def insert_episode(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    task_id: str,
    started_at: str,
    injection_task_id: str | None = None,
    utility: bool | None = None,
    security: bool | None = None,
    ended_at: str | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO episode
            (run_id, task_id, injection_task_id, utility, security, started_at, ended_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            task_id,
            injection_task_id,
            None if utility is None else int(utility),
            None if security is None else int(security),
            started_at,
            ended_at,
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)  # type: ignore[arg-type]


def insert_step(
    conn: sqlite3.Connection,
    *,
    episode_id: int,
    step_index: int,
    role: str,
    content: str,
    timestamp: str,
) -> int:
    """Does not commit — wrap a batch of these in `transaction(conn)`."""
    cursor = conn.execute(
        """
        INSERT INTO step (episode_id, step_index, role, content, timestamp)
        VALUES (?, ?, ?, ?, ?)
        """,
        (episode_id, step_index, role, content, timestamp),
    )
    return int(cursor.lastrowid)  # type: ignore[arg-type]


def insert_tool_call(
    conn: sqlite3.Connection,
    *,
    step_id: int,
    tool_name: str,
    arguments_json: str,
    timestamp: str,
    result_json: str | None = None,
    blocked_by_defense: str | None = None,
) -> int:
    """Does not commit — wrap a batch of these in `transaction(conn)`."""
    cursor = conn.execute(
        """
        INSERT INTO tool_call
            (step_id, tool_name, arguments_json, result_json, blocked_by_defense, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (step_id, tool_name, arguments_json, result_json, blocked_by_defense, timestamp),
    )
    return int(cursor.lastrowid)  # type: ignore[arg-type]


def insert_defense_event(
    conn: sqlite3.Connection,
    *,
    episode_id: int,
    defense_name: str,
    hook: str,
    verdict: str,
    timestamp: str,
    step_id: int | None = None,
    detail_json: str | None = None,
) -> int:
    """Does not commit — wrap a batch of these in `transaction(conn)`."""
    cursor = conn.execute(
        """
        INSERT INTO defense_event
            (episode_id, step_id, defense_name, hook, verdict, detail_json, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (episode_id, step_id, defense_name, hook, verdict, detail_json, timestamp),
    )
    return int(cursor.lastrowid)  # type: ignore[arg-type]


def insert_cost_record(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    episode_id: int,
    model: str,
    tokens_in: int,
    tokens_out: int,
    wall_ms: int,
    usd: float,
    timestamp: str,
    cache_hit: bool = False,
) -> int:
    """Does not commit — wrap a batch of these in `transaction(conn)`."""
    cursor = conn.execute(
        """
        INSERT INTO cost_record
            (run_id, episode_id, model, tokens_in, tokens_out, wall_ms, usd, cache_hit, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, episode_id, model, tokens_in, tokens_out, wall_ms, usd, int(cache_hit), timestamp),
    )
    return int(cursor.lastrowid)  # type: ignore[arg-type]
