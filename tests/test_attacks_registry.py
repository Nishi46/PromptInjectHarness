import pytest

from injection_pareto.attacks import resolve_attack_name


def test_resolve_attack_name_maps_naive_to_agentdojo_direct() -> None:
    assert resolve_attack_name("naive") == "direct"


@pytest.mark.parametrize("name", ["ignore_previous", "important_instructions"])
def test_resolve_attack_name_passes_through_agentdojo_native_names(name: str) -> None:
    assert resolve_attack_name(name) == name


def test_resolve_attack_name_raises_clear_error_for_unknown_name() -> None:
    with pytest.raises(ValueError, match="Unknown attack"):
        resolve_attack_name("not_a_real_attack")
