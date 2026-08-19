import dataclasses
from pathlib import Path

import pytest

from injection_pareto.mcp.loader import load_server
from injection_pareto.mcp.poisoned import POISONED_CASES, SUB_FAMILIES, PoisonedCase
from injection_pareto.mcp.types import MCPSpecError, MockServer

SERVERS_DIR = Path(__file__).parent.parent / "src" / "injection_pareto" / "mcp" / "servers"


def _load_target_server(case: PoisonedCase) -> MockServer:
    return load_server(SERVERS_DIR / f"{case.target_server}.yaml")


# ---------------------------------------------------------------------------
# structure of the case set: the original 40 S3-04 cases (10 per
# sub-family) plus S5-04's own `poison_body_exfil_email_get_email`
# `direct_instruction` demonstration case (see
# `docs/notes/architectural_defenses.md`'s "S5-04" section for why it's a
# genuinely new case rather than a variant of an existing one).
# ---------------------------------------------------------------------------


def test_exactly_forty_one_cases_are_authored() -> None:
    assert len(POISONED_CASES) == 41


def test_exactly_ten_cases_per_sub_family_plus_the_one_s5_04_case() -> None:
    counts = {family: 0 for family in SUB_FAMILIES}
    for case in POISONED_CASES:
        counts[case.sub_family] += 1
    expected = dict.fromkeys(SUB_FAMILIES, 10)
    expected["direct_instruction"] += 1
    assert counts == expected


def test_every_case_has_a_unique_id() -> None:
    ids = [case.id for case in POISONED_CASES]
    assert len(ids) == len(set(ids))


def test_every_case_targets_a_real_server_and_tool() -> None:
    for case in POISONED_CASES:
        server = _load_target_server(case)
        assert server.get_tool(case.target_tool) is not None, (
            f"{case.id}: server {case.target_server!r} has no tool {case.target_tool!r}"
        )


def test_cases_are_spread_across_at_least_ten_of_the_fifteen_servers() -> None:
    servers_touched = {case.target_server for case in POISONED_CASES}
    assert len(servers_touched) >= 10


# ---------------------------------------------------------------------------
# apply() mutates only the target tool's description (one representative
# case per sub-family, per the checklist -- plus a full 40-case sweep below
# for the "no leak into tool output" check).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case_id",
    [
        "poison_direct_instruction_analytics_query_metric",
        "poison_fake_usage_note_document_signing_get_document_status",
        "poison_fake_precondition_knowledge_base_get_article",
        "poison_cross_tool_redirection_code_search_list_repositories",
    ],
)
def test_apply_mutates_only_the_target_tools_description(case_id: str) -> None:
    case = next(c for c in POISONED_CASES if c.id == case_id)
    original_server = _load_target_server(case)

    mutated_server = case.apply(original_server)

    assert mutated_server.name == original_server.name
    assert mutated_server.description == original_server.description
    assert mutated_server.stateful == original_server.stateful

    for original_tool, mutated_tool in zip(
        original_server.tools, mutated_server.tools, strict=True
    ):
        assert original_tool.name == mutated_tool.name
        if mutated_tool.name == case.target_tool:
            assert mutated_tool.description != original_tool.description
            assert mutated_tool.description.startswith(original_tool.description)
            assert case.injection_text in mutated_tool.description
        else:
            assert mutated_tool == original_tool  # byte-identical, untouched

    # the original server object itself is never mutated in place
    original_target_tool = original_server.get_tool(case.target_tool)
    mutated_target_tool = mutated_server.get_tool(case.target_tool)
    assert original_target_tool is not None
    assert mutated_target_tool is not None
    assert original_target_tool.description != mutated_target_tool.description


def test_apply_raises_for_a_server_that_does_not_match_target_server() -> None:
    case = POISONED_CASES[0]
    assert case.target_server == "analytics"  # sanity: this is a real mismatch below
    wrong_server = load_server(SERVERS_DIR / "ticketing.yaml")

    with pytest.raises(MCPSpecError, match="targets server"):
        case.apply(wrong_server)


def test_apply_raises_for_a_tool_the_server_does_not_have() -> None:
    case = dataclasses.replace(POISONED_CASES[0], target_tool="does_not_exist")
    server = _load_target_server(case)

    with pytest.raises(MCPSpecError, match="does not have"):
        case.apply(server)


# ---------------------------------------------------------------------------
# no leak into tool *output* -- every field except the target tool's
# description is untouched, for all 40 cases (not just the 4 samples above).
# Automates the checklist's "render and eyeball" check as a permanent,
# scored assertion rather than a one-time manual pass.
# ---------------------------------------------------------------------------


def test_no_case_leaks_its_injection_into_tool_output_data() -> None:
    for case in POISONED_CASES:
        original_server = _load_target_server(case)
        mutated_server = case.apply(original_server)

        for original_tool, mutated_tool in zip(
            original_server.tools, mutated_server.tools, strict=True
        ):
            assert mutated_tool.default_response == original_tool.default_response, case.id
            assert mutated_tool.default_error == original_tool.default_error, case.id
            assert mutated_tool.variants == original_tool.variants, case.id
            assert mutated_tool.parameters == original_tool.parameters, case.id
            if mutated_tool.name != case.target_tool:
                assert mutated_tool.description == original_tool.description, case.id


def test_render_all_forty_final_descriptions_to_a_scratch_file(tmp_path: Path) -> None:
    """The checklist's manual/scripted eyeball check, kept runnable: renders
    every case's final (poisoned) tool description to a scratch file so a
    human can skim it for anything that looks like it also contaminated
    tool *output* rather than staying confined to the schema."""
    lines = []
    for case in POISONED_CASES:
        server = _load_target_server(case)
        mutated = case.apply(server)
        tool = mutated.get_tool(case.target_tool)
        assert tool is not None
        lines.append(f"[{case.sub_family}] {case.target_server}.{case.target_tool}:")
        lines.append(f"  {tool.description}")
        lines.append(f"  goal: {case.injection_goal}")
        lines.append("")

    out_path = tmp_path / "poisoned_descriptions.txt"
    out_path.write_text("\n".join(lines))

    rendered = out_path.read_text()
    assert rendered.count("[direct_instruction]") == 11  # 10 S3-04 + S5-04's demo case
    assert rendered.count("[fake_usage_note]") == 10
    assert rendered.count("[fake_precondition]") == 10
    assert rendered.count("[cross_tool_redirection]") == 10
