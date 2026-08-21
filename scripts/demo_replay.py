"""S7-07 -- replays a real, already-recorded episode's trace to the
terminal at a readable pace, meant to be screen-recorded into a short demo
GIF. Reuses `trace.queries.reconstruct_episode` (the project's own
episode-reconstruction machinery, already used by `docs/reproduction.md`-
style inspection) rather than re-querying `step`/`tool_call`/
`defense_event` by hand.

This script only ever *replays* rows already sitting in a trace DB -- it
never re-runs a live episode -- so what you see is guaranteed to be the
actual documented result (`docs/notes/architectural_defenses.md`'s S5-04
section has the full write-up), not a fresh, possibly-different live
outcome. That's a deliberate choice, not a shortcut: a live re-run against
a real model could legitimately come out differently episode to episode,
and the point of a demo is to show the specific, already-inspected result
the project's own docs describe.

Default pair: the real S5-04 data-exfiltration demonstration
(`runs/local/s5_04_demo/trace.db`) -- episode 1 (`no_defense`, a real
tool-result account number leaks into an otherwise-legitimate reply) vs.
episode 3 (`capability_enforcement`, the identical attempted call blocked,
citing the exact tainted value and why).

Usage:
  .venv/bin/python scripts/demo_replay.py                     # default pair, readable pace
  .venv/bin/python scripts/demo_replay.py --delay 0            # instant -- testing, not recording
  .venv/bin/python scripts/demo_replay.py --episode 1           # replay just one episode
  .venv/bin/python scripts/demo_replay.py --db PATH --episode 5 --episode 6
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

from injection_pareto.trace import connect, reconstruct_episode

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"

_DEFAULT_DB = Path("runs/local/s5_04_demo/trace.db")
_DEFAULT_EPISODES = (1, 3)


def _episode_header(conn: sqlite3.Connection, episode_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT r.defense_stack AS defense_stack, r.model AS model, e.task_id AS task_id, "
        "e.injection_task_id AS injection_task_id, e.security AS security, e.utility AS utility "
        "FROM episode e JOIN run r ON e.run_id = r.id WHERE e.id = ?",
        (episode_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"no episode {episode_id} in this trace DB")
    return row


def _truncate(text: str, n: int = 220) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


def _print_slow(text: str, *, delay: float) -> None:
    print(text)
    if delay:
        time.sleep(delay)


def replay_episode(conn: sqlite3.Connection, episode_id: int, *, delay: float) -> None:
    header = _episode_header(conn, episode_id)
    trace = reconstruct_episode(conn, episode_id)

    _print_slow(
        f"\n{_BOLD}{_CYAN}=== episode {episode_id} -- defense: {header['defense_stack']} "
        f"-- model: {header['model']} ==={_RESET}",
        delay=delay,
    )
    _print_slow(
        f"{_DIM}task: {header['task_id']}  injection: {header['injection_task_id']}{_RESET}",
        delay=delay,
    )

    for step in trace.steps:
        if step.role == "system":
            continue
        if step.role == "user":
            _print_slow(f"\n{_BOLD}user:{_RESET} {step.content}", delay=delay)
            continue
        if step.content and not step.tool_calls:
            _print_slow(f"\n{_BOLD}assistant:{_RESET} {step.content}", delay=delay)
            continue
        for tc in step.tool_calls:
            arguments = json.loads(tc.arguments_json)
            _print_slow(
                f"\n{_BOLD}assistant calls{_RESET} {_YELLOW}{tc.tool_name}{_RESET}({arguments})",
                delay=delay,
            )
            result = json.loads(tc.result_json) if tc.result_json else None
            if tc.blocked_by_defense:
                # Shown in full, not truncated -- the exact reason is the
                # whole point of the demo.
                _print_slow(
                    f"  {_RED}{_BOLD}BLOCKED by {tc.blocked_by_defense}{_RESET}{_RED} "
                    f"-- {str(result).replace(chr(10), ' ')}{_RESET}",
                    delay=delay,
                )
            else:
                _print_slow(f"  {_GREEN}-> {_truncate(str(result))}{_RESET}", delay=delay)

    _print_slow(
        f"\n{_DIM}security={bool(header['security'])}  utility={bool(header['utility'])}{_RESET}\n",
        delay=delay,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=_DEFAULT_DB)
    parser.add_argument("--episode", type=int, action="append", default=None)
    parser.add_argument(
        "--delay",
        type=float,
        default=0.7,
        help="seconds to pause after each printed line (default 0.7; use 0 for instant/testing)",
    )
    args = parser.parse_args()
    episode_ids = args.episode or list(_DEFAULT_EPISODES)

    conn = connect(args.db)
    try:
        for episode_id in episode_ids:
            replay_episode(conn, episode_id, delay=args.delay)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
