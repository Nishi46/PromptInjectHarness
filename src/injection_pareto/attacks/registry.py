from __future__ import annotations

from agentdojo.attacks import attack_registry as _agentdojo_attack_registry

# Canonical attack-family name (as declared in an experiment config's
# `attacks:` list, e.g. `attacks: [naive, ignore_previous,
# important_instructions]`) -> the name AgentDojo's own `load_attack`
# resolves. `adapters/agentdojo_adapter.py` already drives attacks entirely
# through AgentDojo's `load_attack(attack_name, suite, pipeline)` (it builds
# the `injections` dict AgentDojo's `run_task_with_pipeline` expects), and
# AgentDojo already implements naive/ignore-previous/important-instructions
# as `direct`/`ignore_previous`/`important_instructions` in its own registry
# (`agentdojo.attacks.baseline_attacks` /
# `agentdojo.attacks.important_instructions_attacks`, registered as an import
# side effect the moment `agentdojo.attacks.attack_registry` is imported,
# since `agentdojo/attacks/__init__.py` eagerly imports its built-in attack
# modules). Reimplementing those three would drift from the exact attack
# AgentDojo's own scoring assumes, so this registry reuses them rather than
# building a parallel implementation the adapter would never call.
#
# S2-02's two novel families (`context_completion`, `encoding_obfuscation`)
# don't exist in AgentDojo. They'll be implemented as `BaseAttack` subclasses
# and registered into that same `ATTACKS` dict via
# `agentdojo.attacks.attack_registry.register_attack` -- the same mechanism
# AgentDojo's own built-ins use -- so `load_attack` (and this registry) picks
# them up with no adapter changes. Entries below are added as each lands.
_FAMILY_TO_AGENTDOJO_NAME: dict[str, str] = {
    "naive": "direct",
    "ignore_previous": "ignore_previous",
    "important_instructions": "important_instructions",
}


def resolve_attack_name(name: str) -> str:
    """Map one of our project's canonical attack-family names to the name
    AgentDojo's `load_attack` resolves. Raises a clear, typed error for
    anything not registered under either name (e.g. an S2-02 family that
    hasn't landed yet, or a typo)."""
    agentdojo_name = _FAMILY_TO_AGENTDOJO_NAME.get(name, name)
    if agentdojo_name not in _agentdojo_attack_registry.ATTACKS:
        raise ValueError(
            f"Unknown attack {name!r}; registered attack families: "
            f"{sorted(_FAMILY_TO_AGENTDOJO_NAME)}"
        )
    return agentdojo_name
