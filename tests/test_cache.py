from pathlib import Path
from typing import Any

from injection_pareto.cache import ResponseCache
from injection_pareto.clients.base import ModelRequest, ModelResponse
from injection_pareto.clients.cached import CachedModelClient
from injection_pareto.types import CostRecord, Message


class CountingClient:
    """Stub `ModelClient` that counts how many times `generate` actually ran."""

    def __init__(self) -> None:
        self.cache_model_id = "stub:model"
        self.call_count = 0

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.call_count += 1
        return ModelResponse(
            text=f"response #{self.call_count}",
            tool_calls=[],
            tokens_in=10,
            tokens_out=5,
            wall_ms=100,
            cost=CostRecord(usd=0.01, tokens_in=10, tokens_out=5),
            raw={"call": self.call_count},
        )


def _request() -> ModelRequest:
    return ModelRequest(messages=[Message(role="user", content="hi")])


def test_identical_request_hits_cache_on_second_call(tmp_path: Path) -> None:
    stub = CountingClient()
    cache = ResponseCache(cache_dir=tmp_path / "responses")
    client = CachedModelClient(stub, cache)

    first = client.generate(_request())
    second = client.generate(_request())

    assert stub.call_count == 1
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.text == first.text
    assert second.cost.usd == 0.0


def test_no_cache_forces_fresh_call_even_with_existing_entry(tmp_path: Path) -> None:
    stub = CountingClient()
    cache = ResponseCache(cache_dir=tmp_path / "responses")

    warm_client = CachedModelClient(stub, cache)
    warm_client.generate(_request())
    assert stub.call_count == 1

    bypass_client = CachedModelClient(stub, cache, no_cache=True)
    result = bypass_client.generate(_request())

    assert stub.call_count == 2
    assert result.cache_hit is False


def test_no_cache_does_not_write_to_the_cache(tmp_path: Path) -> None:
    stub = CountingClient()
    cache = ResponseCache(cache_dir=tmp_path / "responses")

    bypass_client = CachedModelClient(stub, cache, no_cache=True)
    bypass_client.generate(_request())
    assert stub.call_count == 1

    warm_client = CachedModelClient(stub, cache)
    result = warm_client.generate(_request())

    assert stub.call_count == 2  # no cache entry was written by the --no-cache call
    assert result.cache_hit is False


def test_cache_key_ignores_dict_key_order(tmp_path: Path) -> None:
    stub = CountingClient()
    cache = ResponseCache(cache_dir=tmp_path / "responses")
    client = CachedModelClient(stub, cache)

    params_a: dict[str, Any] = {"temperature": 0.0, "max_tokens": 100}
    params_b: dict[str, Any] = {"max_tokens": 100, "temperature": 0.0}

    client.generate(ModelRequest(messages=[Message(role="user", content="hi")], params=params_a))
    result = client.generate(
        ModelRequest(messages=[Message(role="user", content="hi")], params=params_b)
    )

    assert stub.call_count == 1
    assert result.cache_hit is True
