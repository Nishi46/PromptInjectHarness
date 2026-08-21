from __future__ import annotations

from injection_pareto.cache import ResponseCache
from injection_pareto.clients.base import ModelClient
from injection_pareto.clients.cached import CachedModelClient
from injection_pareto.clients.google_ai_studio import GoogleAIStudioClient
from injection_pareto.clients.groq import GroqClient
from injection_pareto.clients.ollama import OllamaClient
from injection_pareto.clients.openrouter import OpenRouterClient
from injection_pareto.config import ModelSpec


def build_model_client(
    spec: ModelSpec, *, cache: ResponseCache | None = None, no_cache: bool = False
) -> ModelClient:
    """Resolve a config `ModelSpec` (S1-06) into a real client (S1-04),
    wrapped with the response cache (S1-05) unless one isn't supplied."""
    if spec.provider == "ollama":
        client: ModelClient = OllamaClient(spec.model, digest=spec.digest)
    elif spec.provider == "groq":
        client = GroqClient(spec.model)
    elif spec.provider == "google_ai_studio":
        client = GoogleAIStudioClient(spec.model)
    elif spec.provider == "openrouter":
        client = OpenRouterClient(spec.model)
    else:
        raise ValueError(
            f"No client implementation for provider {spec.provider!r} (model {spec.id!r}); "
            "add one in clients/factory.py."
        )

    if cache is None:
        return client
    return CachedModelClient(client, cache, no_cache=no_cache)
