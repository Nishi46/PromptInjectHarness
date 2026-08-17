from pathlib import Path

import pytest

from injection_pareto.cache import ResponseCache
from injection_pareto.clients.cached import CachedModelClient
from injection_pareto.clients.factory import build_model_client
from injection_pareto.clients.groq import GroqClient
from injection_pareto.clients.ollama import OllamaClient
from injection_pareto.config.schema import ModelSpec


def test_build_model_client_resolves_ollama() -> None:
    spec = ModelSpec(id="L1", provider="ollama", model="llama3.2:3b", digest="sha256:abc")

    client = build_model_client(spec)

    assert isinstance(client, OllamaClient)
    assert client.digest == "sha256:abc"


def test_build_model_client_resolves_groq(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    spec = ModelSpec(id="L5", provider="groq", model="llama-3.3-70b-versatile")

    client = build_model_client(spec, cache=None)

    assert isinstance(client, GroqClient)


def test_build_model_client_wraps_with_cache_when_provided(tmp_path: Path) -> None:
    spec = ModelSpec(id="L1", provider="ollama", model="llama3.2:3b")
    cache = ResponseCache(cache_dir=tmp_path / "responses")

    client = build_model_client(spec, cache=cache)

    assert isinstance(client, CachedModelClient)


def test_build_model_client_rejects_unknown_provider() -> None:
    spec = ModelSpec(id="X", provider="not_a_real_provider", model="whatever")

    with pytest.raises(ValueError, match="No client implementation"):
        build_model_client(spec)
