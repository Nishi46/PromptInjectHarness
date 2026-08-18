from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ConfigError(ValueError):
    """A malformed experiment config. The message names the bad field."""


@dataclass
class ModelSpec:
    id: str
    provider: str
    model: str
    digest: str | None = None

    @staticmethod
    def from_dict(data: dict[str, Any], *, index: int) -> ModelSpec:
        missing = [k for k in ("id", "provider", "model") if k not in data]
        if missing:
            raise ConfigError(
                f"models[{index}] is missing required field(s): {', '.join(missing)}"
            )
        return ModelSpec(
            id=data["id"], provider=data["provider"], model=data["model"], digest=data.get("digest")
        )


@dataclass
class OutputConfig:
    trace_db: str

    @staticmethod
    def from_dict(data: dict[str, Any]) -> OutputConfig:
        if "trace_db" not in data:
            raise ConfigError("output.trace_db is required")
        return OutputConfig(trace_db=data["trace_db"])


@dataclass
class ExperimentConfig:
    """One YAML file declaring the `models × defenses × suites × attacks`
    cartesian product to run, plus which tasks each suite runs (S2-10).
    `expand_run_specs` (loader.py) flattens it."""

    name: str
    models: list[ModelSpec]
    defenses: list[str]
    suites: list[str]
    attacks: list[str | None]
    output: OutputConfig
    # Per-suite user-task IDs (S2-10 -- the dimension S1-06 explicitly
    # deferred). Keyed by suite name since one config can span multiple
    # suites, each with its own task-ID namespace.
    tasks: dict[str, list[str]]
    # Per-suite injection-task ID, used for every non-None attack in that
    # suite. One representative injection task per suite (not a full
    # injection-task × attack cross product) is a deliberate simplification
    # -- Appendix A.2's grid sizing (`6 def × 5 atk × 12 tasks = 360`) only
    # comes out right under this reading; matches every attack this project
    # has run against so far (S1-08 through S2-09, all `injection_task_0`).
    # Optional at the config level -- a benign-only config (e.g.
    # `configs/smoke.yaml`) never needs it; `expand_run_specs` requires an
    # entry only for suites actually paired with a non-None attack.
    injection_tasks: dict[str, str]

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ExperimentConfig:
        required = ("name", "models", "defenses", "suites", "attacks", "output", "tasks")
        missing = [k for k in required if k not in data]
        if missing:
            raise ConfigError(
                f"config is missing required top-level field(s): {', '.join(missing)}"
            )

        for field_name in ("models", "defenses", "suites", "attacks"):
            value = data[field_name]
            if not isinstance(value, list) or not value:
                raise ConfigError(f"config.{field_name} must be a non-empty list")

        if not isinstance(data["tasks"], dict):
            raise ConfigError("config.tasks must be a mapping of suite name -> list of task IDs")

        models = [ModelSpec.from_dict(m, index=i) for i, m in enumerate(data["models"])]
        output = OutputConfig.from_dict(data["output"])

        return ExperimentConfig(
            name=data["name"],
            models=models,
            defenses=list(data["defenses"]),
            suites=list(data["suites"]),
            attacks=list(data["attacks"]),
            output=output,
            tasks={suite: list(task_ids) for suite, task_ids in data["tasks"].items()},
            injection_tasks=dict(data.get("injection_tasks") or {}),
        )


@dataclass
class RunSpec:
    """One point in the `model × defense × suite × attack` cartesian
    product, carrying that suite's full task list (S2-10) -- the sweep
    runner iterates `tasks` to reach every individual episode."""

    model: ModelSpec
    defense: str
    suite: str
    attack: str | None
    tasks: list[str]
    injection_task: str | None
