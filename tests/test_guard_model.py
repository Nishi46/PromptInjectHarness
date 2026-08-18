import json
from typing import Any

from injection_pareto.clients.base import ModelRequest, ModelResponse
from injection_pareto.defenses import GuardModel, resolve_defense
from injection_pareto.types import CostRecord, DefenseContext, ToolResult, Verdict


def _detail(reason: str | None) -> dict[str, Any]:
    assert reason is not None
    result: dict[str, Any] = json.loads(reason)
    return result


class _FakeGuardClient:
    """Minimal `ModelClient` stub: returns a fixed score string, tracks how
    many times it was called, and reports a nonzero cost per call so
    `GuardModel.cost()` has something real to sum."""

    cache_model_id = "fake:guard"

    def __init__(self, score_text: str) -> None:
        self.score_text = score_text
        self.calls = 0

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        return ModelResponse(
            text=self.score_text,
            tool_calls=[],
            tokens_in=10,
            tokens_out=2,
            wall_ms=5,
            cost=CostRecord(usd=0.001, tokens_in=10, tokens_out=2),
            raw={},
        )


def _tool_result(content: str = "some tool output") -> ToolResult:
    return ToolResult(tool_call_id="1", content=content)


def test_high_score_blocks() -> None:
    defense = GuardModel(_FakeGuardClient("0.9"), threshold=0.5)

    result = defense.on_tool_result(DefenseContext(), _tool_result())

    assert result.verdict is Verdict.BLOCK
    assert result.value.content == "some tool output"  # content never mutated
    detail = _detail(result.reason)
    assert detail["score"] == 0.9
    assert detail["threshold"] == 0.5
    assert detail["parsed"] is True


def test_low_score_allows() -> None:
    defense = GuardModel(_FakeGuardClient("0.1"), threshold=0.5)

    result = defense.on_tool_result(DefenseContext(), _tool_result())

    assert result.verdict is Verdict.ALLOW
    detail = _detail(result.reason)
    assert detail["score"] == 0.1


def test_unparseable_response_fails_open_and_flags_unparsed() -> None:
    defense = GuardModel(_FakeGuardClient("not a number"), threshold=0.5)

    result = defense.on_tool_result(DefenseContext(), _tool_result())

    assert result.verdict is Verdict.ALLOW
    detail = _detail(result.reason)
    assert detail["score"] == 0.0
    assert detail["parsed"] is False


def test_score_is_clamped_to_unit_interval() -> None:
    defense = GuardModel(_FakeGuardClient("5.0"), threshold=0.5)

    result = defense.on_tool_result(DefenseContext(), _tool_result())

    assert _detail(result.reason)["score"] == 1.0


def test_cost_sums_guard_calls_across_the_episode() -> None:
    client = _FakeGuardClient("0.2")
    defense = GuardModel(client)

    defense.on_tool_result(DefenseContext(), _tool_result("first"))
    defense.on_tool_result(DefenseContext(), _tool_result("second"))

    assert client.calls == 2
    assert len(defense.responses) == 2
    cost = defense.cost()
    assert cost.usd == 0.002
    assert cost.tokens_in == 20
    assert cost.tokens_out == 4


def test_on_pre_generate_and_on_pre_tool_call_are_pass_throughs() -> None:
    from injection_pareto.types import Message, ToolCall

    defense = GuardModel(_FakeGuardClient("0.1"))
    context = DefenseContext()
    messages = [Message(role="user", content="hi")]
    tool_call = ToolCall(id="1", name="send_email", arguments={})

    assert defense.on_pre_generate(context, messages).value is messages
    assert defense.on_pre_tool_call(context, tool_call).value is tool_call


def test_resolve_defense_returns_guard_model_instance() -> None:
    assert isinstance(resolve_defense("guard_model"), GuardModel)
