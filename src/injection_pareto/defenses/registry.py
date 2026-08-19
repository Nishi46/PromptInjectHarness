from __future__ import annotations

from collections.abc import Callable

from injection_pareto.defenses.base import Defense
from injection_pareto.defenses.canary import Canary
from injection_pareto.defenses.capability_enforcement import CapabilityEnforcement
from injection_pareto.defenses.dual_llm import DualLLM
from injection_pareto.defenses.guard_model import GuardModel
from injection_pareto.defenses.instructional_prevention import InstructionalPrevention
from injection_pareto.defenses.no_defense import NoDefense
from injection_pareto.defenses.sink_policy import _DEFAULT_SINK_TOOLS_BY_SUITE
from injection_pareto.defenses.spotlighting import Spotlighting
from injection_pareto.defenses.stack import DefenseStack
from injection_pareto.defenses.tool_allowlist import ToolAllowlist

# Joins two or more registered defense names into one composed run
# (S6-01's composition matrix), e.g. `"spotlighting+guard_model"`. A config
# just lists a composite name as one of its `defenses:` entries -- no
# schema change needed, since `defenses: list[str]` already accepts an
# arbitrary string.
_COMPOSITE_NAME_DELIMITER = "+"


def _default_capability_enforcement() -> Defense:
    """S5-04's real per-suite sink-tool policy (`sink_policy.py`), wired in
    as `capability_enforcement`'s registry default -- `CapabilityEnforcement`
    itself stays a plain `sink_tools=None`-defaulting class (S5-03's own
    tests exercise it against an injected fake policy); only the registry
    needs to know about the real one, so this is the one small factory
    function the S5-03 checklist flagged as possibly-needed."""
    return CapabilityEnforcement(sink_tools=_DEFAULT_SINK_TOOLS_BY_SUITE)


# One entry per config-declared defense name (RunSpec.defense). Sprint 2
# (S2-03..S2-07) adds the real defenses here as they land; Sprint 5
# (S5-01..) adds the architectural defenses.
_DEFENSE_REGISTRY: dict[str, Callable[[], Defense]] = {
    "no_defense": NoDefense,
    "spotlighting": Spotlighting,
    "instructional_prevention": InstructionalPrevention,
    "guard_model": GuardModel,
    "tool_allowlist": ToolAllowlist,
    "canary": Canary,
    "dual_llm": DualLLM,
    "capability_enforcement": _default_capability_enforcement,
}


def resolve_defense(name: str) -> Defense:
    if name not in _DEFENSE_REGISTRY:
        raise ValueError(
            f"Unknown defense {name!r}; registered defenses: {sorted(_DEFENSE_REGISTRY)}"
        )
    return _DEFENSE_REGISTRY[name]()


def resolve_defense_stack(name: str) -> DefenseStack:
    """Resolves a config-declared defense name into a `DefenseStack` --
    `name` may be a single registered name (identical behavior to
    `DefenseStack([(name, resolve_defense(name))])` today) or a
    `"+"`-joined composite (S6-01, e.g. `"spotlighting+guard_model"`),
    resolved member by member via the existing `resolve_defense` so an
    unknown segment raises the exact same `ValueError`, naming that
    segment specifically -- splitting first means a typo'd second member
    never masks a typo'd first one behind a generic "stack" error."""
    parts = name.split(_COMPOSITE_NAME_DELIMITER)
    return DefenseStack([(part, resolve_defense(part)) for part in parts])
