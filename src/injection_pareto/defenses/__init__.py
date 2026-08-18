from injection_pareto.defenses.base import Defense
from injection_pareto.defenses.no_defense import NoDefense
from injection_pareto.defenses.registry import resolve_defense
from injection_pareto.defenses.spotlighting import Spotlighting
from injection_pareto.defenses.stack import DefenseStack

__all__ = ["Defense", "DefenseStack", "NoDefense", "Spotlighting", "resolve_defense"]
