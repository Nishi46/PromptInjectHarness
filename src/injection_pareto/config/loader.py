from __future__ import annotations

import itertools
from pathlib import Path

import yaml

from injection_pareto.config.schema import ConfigError, ExperimentConfig, RunSpec


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
    produces it."""
    specs = []
    for model, defense, suite, attack in itertools.product(
        config.models, config.defenses, config.suites, config.attacks
    ):
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
