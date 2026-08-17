-- Trace schema (S1-03). One row per event; a full episode is reconstructable
-- by joining step + tool_call + defense_event on episode_id, ordered by time.

CREATE TABLE IF NOT EXISTS run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    config_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    defense_stack TEXT NOT NULL,
    suite TEXT NOT NULL,
    attack TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS episode (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES run(id),
    task_id TEXT NOT NULL,
    injection_task_id TEXT,
    utility INTEGER,
    security INTEGER,
    started_at TEXT NOT NULL,
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS step (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER NOT NULL REFERENCES episode(id),
    step_index INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_call (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    step_id INTEGER NOT NULL REFERENCES step(id),
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    result_json TEXT,
    blocked_by_defense TEXT,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS defense_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER NOT NULL REFERENCES episode(id),
    step_id INTEGER REFERENCES step(id),
    defense_name TEXT NOT NULL,
    hook TEXT NOT NULL,
    verdict TEXT NOT NULL,
    detail_json TEXT,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cost_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES run(id),
    episode_id INTEGER NOT NULL REFERENCES episode(id),
    model TEXT NOT NULL,
    tokens_in INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL,
    wall_ms INTEGER NOT NULL,
    usd REAL NOT NULL,
    cache_hit INTEGER NOT NULL DEFAULT 0,
    timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_episode_run_id ON episode(run_id);
CREATE INDEX IF NOT EXISTS idx_step_episode_id ON step(episode_id);
CREATE INDEX IF NOT EXISTS idx_tool_call_step_id ON tool_call(step_id);
CREATE INDEX IF NOT EXISTS idx_defense_event_episode_id ON defense_event(episode_id);
CREATE INDEX IF NOT EXISTS idx_defense_event_step_id ON defense_event(step_id);
CREATE INDEX IF NOT EXISTS idx_cost_record_episode_id ON cost_record(episode_id);
CREATE INDEX IF NOT EXISTS idx_cost_record_run_id ON cost_record(run_id);
