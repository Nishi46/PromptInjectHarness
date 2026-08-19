from __future__ import annotations

import itertools
from pathlib import Path

import yaml

from injection_pareto.config.schema import ConfigError, ExperimentConfig, RunSpec
from injection_pareto.mcp.poisoned import get_case
from injection_pareto.mcp.tasks import get_task


def load_config(path: str | Path) -> ExperimentConfig:
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: config must be a YAML mapping at the top level")
    return ExperimentConfig.from_dict(raw)


def expand_run_specs(config: ExperimentConfig) -> list[RunSpec]:
    """Flatten `models × defenses × suites × attacks` into one `RunSpec` per
    combination, each carrying its suite's task list (and, for a non-None
    attack, that suite's injection task). Full parallel/resumable execution
    over this list is S2-10's sweep runner (`sweep/runner.py`) — this just
    produces it.

    The `suite == "mcp"` case (S3-06) reads from `config.mcp_tasks`/
    `config.mcp_poisoned_cases` instead of `config.tasks`/`config.injection_tasks`
    — see `ExperimentConfig`'s docstring on those fields for why they're
    separate. `mcp_poisoned_cases` is keyed by `attack` (the sub-family
    name), not by suite, since a poisoned case's sub-family and its target
    aren't independently choosable the way an AgentDojo attack and
    injection task are.

    For an attacked "mcp" point, `tasks` is further filtered down to only
    the `config.mcp_tasks` entries whose `MCPUserTask.servers` actually
    include the chosen case's `target_server` — found while writing the
    real sweep config: a poisoned case only ever manifests in an episode
    that mounts its target server (S3-01's architecture note), and every
    real S3-03 task mounts exactly one server, so running one
    representative case against the *full* 15-task list would silently
    turn 14 of every 15 "attacked" episodes into episodes that were never
    actually poisoned at all — diluting ASR by mislabeling guaranteed-safe
    episodes as attacked. The unfiltered `config.mcp_tasks` is still used
    in full for the benign pass (`attack is None`), where every task is a
    legitimate utility data point.
    """
    specs = []
    for model, defense, suite, attack in itertools.product(
        config.models, config.defenses, config.suites, config.attacks
    ):
        tasks: list[str] | None
        if suite == "mcp":
            tasks = config.mcp_tasks
            if not tasks:
                raise ConfigError(
                    "config.mcp_tasks must be a non-empty list when suite 'mcp' is used"
                )

            injection_task = None
            if attack is not None:
                injection_task = config.mcp_poisoned_cases.get(attack)
                if injection_task is None:
                    raise ConfigError(
                        f"attack {attack!r} is paired with suite 'mcp' but has no entry in "
                        "config.mcp_poisoned_cases"
                    )
                target_server = get_case(injection_task).target_server
                tasks = [t for t in tasks if target_server in get_task(t).servers]
                if not tasks:
                    raise ConfigError(
                        f"none of config.mcp_tasks mount server {target_server!r}, needed by "
                        f"poisoned case {injection_task!r} (attack {attack!r})"
                    )
        else:
            tasks = config.tasks.get(suite)
            if not tasks:
                raise ConfigError(f"no tasks configured for suite {suite!r} in config.tasks")

            injection_task = None
            if attack is not None:
                injection_task = config.injection_tasks.get(suite)
                if injection_task is None:
                    raise ConfigError(
                        f"suite {suite!r} is paired with attack {attack!r} but has no entry in "
                        "config.injection_tasks"
                    )

        specs.append(
            RunSpec(
                model=model,
                defense=defense,
                suite=suite,
                attack=attack,
                tasks=tasks,
                injection_task=injection_task,
            )
        )
    return specs
