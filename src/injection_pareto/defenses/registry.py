from __future__ import annotations

from collections.abc import Callable

from injection_pareto.defenses.base import Defense
from injection_pareto.defenses.canary import Canary
from injection_pareto.defenses.capability_enforcement import CapabilityEnforcement
from injection_pareto.defenses.dual_llm import DualLLM
from injection_pareto.defenses.guard_model import GuardModel
from injection_pareto.defenses.instructional_prevention import InstructionalPrevention
from injection_pareto.defenses.no_defense import NoDefense
from injection_pareto.defenses.spotlighting import Spotlighting
from injection_pareto.defenses.tool_allowlist import ToolAllowlist

# One entry per config-declared defense name (RunSpec.defense). Sprint 2
# (S2-03..S2-07) adds the real defenses here as they land; Sprint 5
# (S5-01..) adds the architectural defenses. `CapabilityEnforcement`, like
# `GuardModel`/`ToolAllowlist`, has every `__init__` param defaulted, so the
# class itself is already a valid zero-arg `Callable[[], Defense]` -- no
# separate factory function needed (its default `sink_tools=None` is an
# empty policy until S5-04 wires the real one in via this same registry).
_DEFENSE_REGISTRY: dict[str, Callable[[], Defense]] = {
    "no_defense": NoDefense,
    "spotlighting": Spotlighting,
    "instructional_prevention": InstructionalPrevention,
    "guard_model": GuardModel,
    "tool_allowlist": ToolAllowlist,
    "canary": Canary,
    "dual_llm": DualLLM,
    "capability_enforcement": CapabilityEnforcement,
}


def resolve_defense(name: str) -> Defense:
    if name not in _DEFENSE_REGISTRY:
        raise ValueError(
            f"Unknown defense {name!r}; registered defenses: {sorted(_DEFENSE_REGISTRY)}"
        )
    return _DEFENSE_REGISTRY[name]()
