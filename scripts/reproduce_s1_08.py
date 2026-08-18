"""S1-08 — reproduce an AgentDojo baseline through our own harness.

Runs the same set of (user_task, injection_task_0) pairs — suite `workspace`,
attack `important_instructions`, model `llama3.2:3b` — through two independent
code paths and prints both aggregates side by side:

  1. Our own harness: `injection_pareto.adapters.run_episode` (S1-07), writing
     a full trace to `runs/local/reproduction/trace.db`.
  2. AgentDojo's own reference pipeline, called directly via
     `TaskSuite.run_task_with_pipeline` and `AgentPipeline.from_config` — the
     same code path their own CLI (`agentdojo.scripts.benchmark`) drives,
     without the subprocess/CLI overhead of re-loading the suite per task.

A real adapter bug shows up as a large, systematic delta between the two. Some
delta is still expected even with a correct adapter: the two pipelines make
independent, non-seeded calls to the same model, so sampling noise alone can
shift a handful of episodes either way at this sample size.

Usage: .venv/bin/python scripts/reproduce_s1_08.py
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("LOCAL_LLM_PORT", "11434")

from agentdojo.agent_pipeline.agent_pipeline import AgentPipeline, PipelineConfig  # noqa: E402
from agentdojo.attacks.attack_registry import load_attack  # noqa: E402
from agentdojo.task_suite.load_suites import get_suite  # noqa: E402

from injection_pareto.adapters import BENCHMARK_VERSION, run_episode  # noqa: E402
from injection_pareto.cache import ResponseCache  # noqa: E402
from injection_pareto.clients.factory import build_model_client  # noqa: E402
from injection_pareto.config.schema import ModelSpec  # noqa: E402
from injection_pareto.defenses import resolve_defense  # noqa: E402
from injection_pareto.defenses.stack import DefenseStack  # noqa: E402
from injection_pareto.trace import connect, init_db, insert_run  # noqa: E402

SUITE_NAME = "workspace"
ATTACK_NAME = "important_instructions"
INJECTION_TASK_ID = "injection_task_0"
MODEL = ModelSpec(id="L1", provider="ollama", model="llama3.2:3b", digest="sha256:a80c4f17acd5")
N_TASKS = 6
TRACE_DB_PATH = Path("runs/local/reproduction/trace.db")


def _task_ids(n: int) -> list[str]:
    suite = get_suite(BENCHMARK_VERSION, SUITE_NAME)
    return list(suite.user_tasks.keys())[:n]


def _aggregate(
    benign_utility: list[bool], attacked_utility: list[bool], security: list[bool]
) -> dict[str, float]:
    # `TaskSuite.run_task_with_pipeline`'s `security` return is the injection
    # task's own `.security()` check: True means the injected goal was
    # actually carried out (attack succeeded), False means it wasn't —
    # confirmed directly against AgentDojo's source (concrete injection-task
    # `security()` implementations check post-environment state for the
    # attacker's effect, e.g. workspace's `injection_task_0` returns True
    # only if the malicious email was actually found sent) and empirically
    # (manually executing the malicious `send_email` call and checking
    # `security()` returns True; an untouched environment returns False).
    # So ASR is `security` averaged directly — no inversion.
    return {
        "benign_utility_rate": sum(benign_utility) / len(benign_utility),
        "attacked_utility_rate": sum(attacked_utility) / len(attacked_utility),
        "targeted_asr": sum(security) / len(security),
    }


def run_our_harness(task_ids: list[str]) -> dict[str, float]:
    TRACE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    cache = ResponseCache(cache_dir=".cache/responses")
    client = build_model_client(MODEL, cache=cache)
    defense_stack = DefenseStack([resolve_defense("no_defense")])

    conn = connect(TRACE_DB_PATH)
    init_db(conn)
    run_id = insert_run(
        conn,
        config_hash="s1-08-reproduction",
        model=MODEL.model,
        defense_stack="no_defense",
        suite=SUITE_NAME,
        attack=ATTACK_NAME,
        started_at="s1-08",
    )

    benign_utility, attacked_utility, security = [], [], []
    for task_id in task_ids:
        r = run_episode(
            conn=conn,
            run_id=run_id,
            suite_name=SUITE_NAME,
            user_task_id=task_id,
            injection_task_id=None,
            model_client=client,
            defense_stack=defense_stack,
            defense_name="no_defense",
            model_name=MODEL.model,
        )
        benign_utility.append(r.utility)
        print(f"  [ours] benign  {task_id}: utility={r.utility} security={r.security}")

        r = run_episode(
            conn=conn,
            run_id=run_id,
            suite_name=SUITE_NAME,
            user_task_id=task_id,
            injection_task_id=INJECTION_TASK_ID,
            model_client=client,
            defense_stack=defense_stack,
            defense_name="no_defense",
            model_name=MODEL.model,
            attack_name=ATTACK_NAME,
        )
        attacked_utility.append(r.utility)
        security.append(r.security)
        print(f"  [ours] attack  {task_id}: utility={r.utility} security={r.security}")

    conn.close()
    return _aggregate(benign_utility, attacked_utility, security)


def run_agentdojo_direct(task_ids: list[str]) -> dict[str, float]:
    suite = get_suite(BENCHMARK_VERSION, SUITE_NAME)
    config = PipelineConfig(
        llm="local",
        model_id=MODEL.model,
        defense=None,
        tool_delimiter="tool",
        system_message_name=None,
        system_message=None,
    )
    pipeline = AgentPipeline.from_config(config)

    benign_utility, attacked_utility, security = [], [], []
    for task_id in task_ids:
        user_task = suite.get_user_task_by_id(task_id)
        utility, _ = suite.run_task_with_pipeline(pipeline, user_task, None, {})
        benign_utility.append(utility)
        print(f"  [agentdojo] benign  {task_id}: utility={utility}")

        injection_task = suite.get_injection_task_by_id(INJECTION_TASK_ID)
        attack = load_attack(ATTACK_NAME, suite, pipeline)
        injections = attack.attack(user_task, injection_task)
        utility, sec = suite.run_task_with_pipeline(pipeline, user_task, injection_task, injections)
        attacked_utility.append(utility)
        security.append(sec)
        print(f"  [agentdojo] attack  {task_id}: utility={utility} security={sec}")

    return _aggregate(benign_utility, attacked_utility, security)


def main() -> None:
    task_ids = _task_ids(N_TASKS)
    print(f"Tasks ({len(task_ids)}): {task_ids}")

    print("\n=== Our harness (S1-07 adapter) ===")
    ours = run_our_harness(task_ids)
    print(ours)

    print("\n=== AgentDojo direct (reference) ===")
    theirs = run_agentdojo_direct(task_ids)
    print(theirs)

    print("\n=== Delta (ours - theirs) ===")
    for key in ours:
        delta = ours[key] - theirs[key]
        print(f"  {key}: ours={ours[key]:.3f} theirs={theirs[key]:.3f} delta={delta:+.3f}")


if __name__ == "__main__":
    main()
