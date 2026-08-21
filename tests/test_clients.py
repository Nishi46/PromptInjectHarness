from typing import Any

import pytest

from injection_pareto.clients import (
    GoogleAIStudioClient,
    GroqClient,
    ModelRequest,
    OllamaClient,
    OpenRouterClient,
    compute_cost,
)
from injection_pareto.types import Message, ToolCall


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
        model="openai/gpt-oss-120b", api_key="test-key", session=session  # type: ignore[arg-type]
    )

    result = client.generate(ModelRequest(messages=[Message(role="user", content="hi")]))

    assert result.text == "hi there"
    assert result.tokens_in == 1000
    assert result.tokens_out == 500
    expected_usd = (1000 / 1_000_000) * 0.15 + (500 / 1_000_000) * 0.60
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
        model="openai/gpt-oss-120b", api_key="test-key", session=session  # type: ignore[arg-type]
    )

    result = client.generate(ModelRequest(messages=[Message(role="user", content="hi")]))

    assert result.text == ""
    assert result.tool_calls[0].id == "call_1"
    assert result.tool_calls[0].arguments == {"query": "prompt injection"}


def test_compute_cost_rejects_unknown_hosted_model() -> None:
    with pytest.raises(ValueError, match="No published rate"):
        compute_cost(provider="groq", model="some-new-model", tokens_in=10, tokens_out=10)


# -- S6-05: OpenRouterClient (OpenAI-compatible, closely mirrors GroqClient) --


def test_openrouter_client_populates_tokens_latency_and_zero_cost_for_a_free_model() -> None:
    fake_response = FakeResponse(
        {
            "choices": [
                {"message": {"role": "assistant", "content": "hi there", "tool_calls": []}}
            ],
            "usage": {"prompt_tokens": 300, "completion_tokens": 40},
        }
    )
    session = FakeSession(fake_response)
    client = OpenRouterClient(
        model="nvidia/nemotron-3.5-lightning:free",
        api_key="test-key",
        session=session,  # type: ignore[arg-type]
    )

    result = client.generate(ModelRequest(messages=[Message(role="user", content="hi")]))

    assert result.text == "hi there"
    assert result.tokens_in == 300
    assert result.tokens_out == 40
    assert result.cost.usd == 0.0  # a real, published $0 rate -- not an ollama-style special case
    _, kwargs = session.calls[0]
    assert kwargs["headers"]["Authorization"] == "Bearer test-key"
    assert session.calls[0][0] == "https://openrouter.ai/api/v1/chat/completions"


def test_openrouter_client_parses_tool_calls_with_json_string_arguments() -> None:
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
                                "function": {"name": "search", "arguments": '{"query": "x"}'},
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
    )
    session = FakeSession(fake_response)
    client = OpenRouterClient(
        model="nvidia/nemotron-3.5-lightning:free",
        api_key="test-key",
        session=session,  # type: ignore[arg-type]
    )

    result = client.generate(ModelRequest(messages=[Message(role="user", content="hi")]))

    assert result.tool_calls[0].id == "call_1"
    assert result.tool_calls[0].name == "search"
    assert result.tool_calls[0].arguments == {"query": "x"}


# -- S6-05: GoogleAIStudioClient (Gemini's own, non-OpenAI-compatible shape) --


def test_google_ai_studio_client_sends_api_key_as_query_param_not_header() -> None:
    fake_response = FakeResponse(
        {
            "candidates": [{"content": {"role": "model", "parts": [{"text": "hi there"}]}}],
            "usageMetadata": {"promptTokenCount": 74, "candidatesTokenCount": 16},
        }
    )
    session = FakeSession(fake_response)
    client = GoogleAIStudioClient(
        model="gemini-3.5-flash", api_key="test-key", session=session  # type: ignore[arg-type]
    )

    result = client.generate(ModelRequest(messages=[Message(role="user", content="hi")]))

    assert result.text == "hi there"
    assert result.tokens_in == 74
    assert result.tokens_out == 16
    expected_usd = (74 / 1_000_000) * 0.075 + (16 / 1_000_000) * 0.30
    assert result.cost.usd == pytest.approx(expected_usd)
    url, kwargs = session.calls[0]
    assert url.endswith("/models/gemini-3.5-flash:generateContent")
    assert kwargs["params"] == {"key": "test-key"}
    assert "headers" not in kwargs or "Authorization" not in kwargs.get("headers", {})


def test_google_ai_studio_client_parses_function_call_parts_with_fabricated_ids() -> None:
    fake_response = FakeResponse(
        {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {"functionCall": {"name": "get_weather", "args": {"city": "Paris"}}}
                        ],
                    }
                }
            ],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 5},
        }
    )
    session = FakeSession(fake_response)
    client = GoogleAIStudioClient(
        model="gemini-3.5-flash", api_key="test-key", session=session  # type: ignore[arg-type]
    )

    result = client.generate(ModelRequest(messages=[Message(role="user", content="hi")]))

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "get_weather"
    assert result.tool_calls[0].arguments == {"city": "Paris"}
    assert result.tool_calls[0].id  # a real, non-empty fabricated id -- Gemini never sends one


def test_google_ai_studio_client_routes_system_message_to_system_instruction() -> None:
    fake_response = FakeResponse(
        {
            "candidates": [{"content": {"role": "model", "parts": [{"text": "ok"}]}}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
        }
    )
    session = FakeSession(fake_response)
    client = GoogleAIStudioClient(
        model="gemini-3.5-flash", api_key="test-key", session=session  # type: ignore[arg-type]
    )

    client.generate(
        ModelRequest(
            messages=[
                Message(role="system", content="be helpful"),
                Message(role="user", content="hi"),
            ]
        )
    )

    _, kwargs = session.calls[0]
    payload = kwargs["json"]
    assert payload["systemInstruction"]["parts"][0]["text"] == "be helpful"
    # The system message must never leak into `contents` as its own turn.
    assert all(c["role"] != "system" for c in payload["contents"])
    assert len(payload["contents"]) == 1


def test_google_ai_studio_client_builds_function_response_by_looking_up_the_calls_name() -> None:
    """Our `Message(role="tool")` only carries `tool_call_id`, not the
    function's name -- Gemini's `functionResponse` part is keyed by name,
    not id, so the client must resolve it from the preceding assistant
    turn's `tool_calls` before it can serialize this message at all."""
    fake_response = FakeResponse(
        {
            "candidates": [{"content": {"role": "model", "parts": [{"text": "done"}]}}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
        }
    )
    session = FakeSession(fake_response)
    client = GoogleAIStudioClient(
        model="gemini-3.5-flash", api_key="test-key", session=session  # type: ignore[arg-type]
    )

    client.generate(
        ModelRequest(
            messages=[
                Message(role="user", content="what's the weather?"),
                Message(
                    role="assistant",
                    content="",
                    tool_calls=[
                        ToolCall(id="abc123", name="get_weather", arguments={"city": "Paris"})
                    ],
                ),
                Message(role="tool", content="sunny", tool_call_id="abc123"),
            ]
        )
    )

    _, kwargs = session.calls[0]
    contents = kwargs["json"]["contents"]
    tool_turn = contents[-1]
    assert tool_turn["role"] == "user"
    function_response = tool_turn["parts"][0]["functionResponse"]
    assert function_response["name"] == "get_weather"
    assert function_response["response"]["result"] == "sunny"


def test_google_ai_studio_client_converts_openai_shaped_tools_to_function_declarations() -> None:
    fake_response = FakeResponse(
        {
            "candidates": [{"content": {"role": "model", "parts": [{"text": "ok"}]}}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
        }
    )
    session = FakeSession(fake_response)
    client = GoogleAIStudioClient(
        model="gemini-3.5-flash", api_key="test-key", session=session  # type: ignore[arg-type]
    )
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the weather.",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
            },
        }
    ]

    client.generate(ModelRequest(messages=[Message(role="user", content="hi")], tools=tools))

    _, kwargs = session.calls[0]
    payload_tools = kwargs["json"]["tools"]
    assert len(payload_tools) == 1
    assert payload_tools[0]["functionDeclarations"][0]["name"] == "get_weather"
