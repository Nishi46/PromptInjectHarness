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
    combination. Full parallel/resumable execution over this list is S2-10's
    sweep runner — this just produces it."""
    return [
        RunSpec(model=model, defense=defense, suite=suite, attack=attack)
        for model, defense, suite, attack in itertools.product(
            config.models, config.defenses, config.suites, config.attacks
        )
    ]
