from typing import Any

import pytest

from injection_pareto.clients import GroqClient, ModelRequest, OllamaClient, compute_cost
from injection_pareto.types import Message


class FakeResponse:
    def __init__(self, json_data: dict[str, Any]) -> None:
        self._json_data = json_data

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self._json_data


class FakeSession:
    """Stands in for `requests.Session` — records calls, never touches the network."""

    def __init__(self, response: FakeResponse) -> None:
        self._response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        return self._response


def test_ollama_client_populates_tokens_latency_and_zero_cost() -> None:
    fake_response = FakeResponse(
        {
            "message": {"role": "assistant", "content": "hello", "tool_calls": []},
            "prompt_eval_count": 100,
            "eval_count": 20,
        }
    )
    session = FakeSession(fake_response)
    client = OllamaClient(model="llama3.2:3b", session=session)  # type: ignore[arg-type]

    result = client.generate(ModelRequest(messages=[Message(role="user", content="hi")]))

    assert result.text == "hello"
    assert result.tokens_in == 100
    assert result.tokens_out == 20
    assert result.cost.usd == 0.0
    assert result.wall_ms >= 0
    assert session.calls[0][0] == "http://localhost:11434/api/chat"


def test_ollama_client_parses_tool_calls() -> None:
    fake_response = FakeResponse(
        {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "send_email", "arguments": {"to": "a@b.com"}}}
                ],
            },
            "prompt_eval_count": 50,
            "eval_count": 10,
        }
    )
    session = FakeSession(fake_response)
    client = OllamaClient(model="llama3.2:3b", session=session)  # type: ignore[arg-type]

    result = client.generate(ModelRequest(messages=[Message(role="user", content="hi")]))

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "send_email"
    assert result.tool_calls[0].arguments == {"to": "a@b.com"}


def test_groq_client_populates_tokens_latency_and_nonzero_cost() -> None:
    fake_response = FakeResponse(
        {
            "choices": [
                {"message": {"role": "assistant", "content": "hi there", "tool_calls": []}}
            ],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
        }
    )
    session = FakeSession(fake_response)
    client = GroqClient(
        model="llama-3.3-70b-versatile", api_key="test-key", session=session  # type: ignore[arg-type]
    )

    result = client.generate(ModelRequest(messages=[Message(role="user", content="hi")]))

    assert result.text == "hi there"
    assert result.tokens_in == 1000
    assert result.tokens_out == 500
    expected_usd = (1000 / 1_000_000) * 0.59 + (500 / 1_000_000) * 0.79
    assert result.cost.usd == pytest.approx(expected_usd)
    assert result.wall_ms >= 0
    _, kwargs = session.calls[0]
    assert kwargs["headers"]["Authorization"] == "Bearer test-key"


def test_groq_client_parses_tool_calls_with_json_string_arguments() -> None:
    fake_response = FakeResponse(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "search",
                                    "arguments": '{"query": "prompt injection"}',
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
    )
    session = FakeSession(fake_response)
    client = GroqClient(
        model="llama-3.3-70b-versatile", api_key="test-key", session=session  # type: ignore[arg-type]
    )

    result = client.generate(ModelRequest(messages=[Message(role="user", content="hi")]))

    assert result.text == ""
    assert result.tool_calls[0].id == "call_1"
    assert result.tool_calls[0].arguments == {"query": "prompt injection"}


def test_compute_cost_rejects_unknown_hosted_model() -> None:
    with pytest.raises(ValueError, match="No published rate"):
        compute_cost(provider="groq", model="some-new-model", tokens_in=10, tokens_out=10)
