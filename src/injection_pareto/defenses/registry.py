from __future__ import annotations

from collections.abc import Callable

from injection_pareto.defenses.base import Defense
from injection_pareto.defenses.guard_model import GuardModel
from injection_pareto.defenses.instructional_prevention import InstructionalPrevention
from injection_pareto.defenses.no_defense import NoDefense
from injection_pareto.defenses.spotlighting import Spotlighting
from injection_pareto.defenses.tool_allowlist import ToolAllowlist

# One entry per config-declared defense name (RunSpec.defense). Sprint 2
# (S2-03..S2-07) adds the real defenses here as they land.
_DEFENSE_REGISTRY: dict[str, Callable[[], Defense]] = {
    "no_defense": NoDefense,
    "spotlighting": Spotlighting,
    "instructional_prevention": InstructionalPrevention,
    "guard_model": GuardModel,
    "tool_allowlist": ToolAllowlist,
}


def resolve_defense(name: str) -> Defense:
    if name not in _DEFENSE_REGISTRY:
        raise ValueError(
            f"Unknown defense {name!r}; registered defenses: {sorted(_DEFENSE_REGISTRY)}"
        )
    return _DEFENSE_REGISTRY[name]()
