"""Registers our project's own attack families into AgentDojo's `ATTACKS`
registry (same `register_attack` decorator mechanism AgentDojo's own built-in
attacks use — see `attacks/registry.py`). Importing this package is what
triggers registration; `attacks/registry.py` imports it for that side effect."""

from injection_pareto.attacks.families.context_completion import ContextCompletionAttack
from injection_pareto.attacks.families.encoding_obfuscation import EncodingObfuscationAttack

__all__ = ["ContextCompletionAttack", "EncodingObfuscationAttack"]
