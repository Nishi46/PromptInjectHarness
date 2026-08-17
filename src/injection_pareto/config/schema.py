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
    cartesian product to run. `expand_run_specs` (loader.py) flattens it."""

    name: str
    models: list[ModelSpec]
    defenses: list[str]
    suites: list[str]
    attacks: list[str | None]
    output: OutputConfig

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ExperimentConfig:
        required = ("name", "models", "defenses", "suites", "attacks", "output")
        missing = [k for k in required if k not in data]
        if missing:
            raise ConfigError(
                f"config is missing required top-level field(s): {', '.join(missing)}"
            )

        for field_name in ("models", "defenses", "suites", "attacks"):
            value = data[field_name]
            if not isinstance(value, list) or not value:
                raise ConfigError(f"config.{field_name} must be a non-empty list")

        models = [ModelSpec.from_dict(m, index=i) for i, m in enumerate(data["models"])]
        output = OutputConfig.from_dict(data["output"])

        return ExperimentConfig(
            name=data["name"],
            models=models,
            defenses=list(data["defenses"]),
            suites=list(data["suites"]),
            attacks=list(data["attacks"]),
            output=output,
        )


@dataclass
class RunSpec:
    """One point in the cartesian product: exactly one model/defense/suite/attack."""

    model: ModelSpec
    defense: str
    suite: str
    attack: str | None
