from __future__ import annotations

import os
import time
import uuid
from typing import Any

import requests

from injection_pareto.clients.base import ModelRequest, ModelResponse
from injection_pareto.clients.costs import compute_cost
from injection_pareto.types import Message, ToolCall

# Gemini's REST API (`generateContent`) is *not* OpenAI-compatible, unlike
# Groq/OpenRouter -- confirmed against Google's own API reference
# (ai.google.dev/api/generate-content, ai.google.dev/api/rest/v1beta/Content)
# before writing this, per S6-05's own checklist instruction not to assume.
# The real differences this module has to bridge:
#
#   - Only two roles exist: "user" and "model" (no "system"/"tool"/
#     "assistant"). A system prompt is a separate top-level
#     `systemInstruction` field, not a `contents[]` entry.
#   - A function call the model requests is a `functionCall` part
#     (`{"name": ..., "args": {...}}`). Gemini *sometimes* includes its
#     own `id` alongside those (observed directly in a real response, not
#     documented in the schema reference this module was written against)
#     -- used when present; when absent, this client fabricates one
#     (`uuid.uuid4()`, mirroring `mcp_adapter.py::_parse_fallback_tool_call`'s
#     same need) purely so the rest of this project's `ToolCall`/
#     `ToolResult` machinery (which is id-keyed) has something to key on.
#   - A `functionCall` part returned alongside a `thoughtSignature` field
#     (a sibling of `functionCall`, not nested inside it) must have that
#     *exact* signature echoed back verbatim on the same part when it's
#     replayed into a later request's history -- found the hard way, via a
#     real `400 INVALID_ARGUMENT` ("Function call is missing a
#     thought_signature...") on the second turn of a real multi-turn
#     sweep episode, not documented clearly enough in Gemini's own docs to
#     have been anticipated. This client caches signatures by tool-call id
#     (`self._thought_signatures`) on the instance, since the same client
#     object is reused for every turn of one episode (`sweep/runner.py`
#     constructs one `model_client` per episode, not per turn).
#   - A function's result is sent back as a `functionResponse` part
#     (`{"name": ..., "response": {...}}`) -- keyed by *name*, not by the
#     id above -- inside a `role: "user"` turn (there is no separate
#     "function"/"tool" role). Our own `Message(role="tool", ...)` only
#     carries `tool_call_id`, not the function's name, so this module
#     rebuilds an id-to-name map from every preceding assistant turn's
#     `tool_calls` before it can serialize a "tool" message.
#   - `tools` is a single object with a `functionDeclarations` array, not
#     one entry per function the way OpenAI's `tools: [{type: "function",
#     function: {...}}, ...]` shape (already what `ModelRequest.tools`
#     holds, built by `_build_tool_schema` in the adapters) is -- this
#     module re-shapes it.
#   - Token usage is `usageMetadata.promptTokenCount`/`candidatesTokenCount`,
#     not `usage.prompt_tokens`/`completion_tokens`.
#   - The API key is a query parameter, not an `Authorization` header.


def _build_contents(
    messages: list[Message], thought_signatures: dict[str, str]
) -> tuple[str | None, list[dict[str, Any]]]:
    """Splits our `Message` list into (system instruction text, Gemini
    `contents[]`). The first `role="system"` message (every adapter sends
    exactly one) becomes `systemInstruction`; everything else is mapped to
    a `{"role": "user"|"model", "parts": [...]}` entry. `thought_signatures`
    (id -> signature, populated by `GoogleAIStudioClient.generate` as it
    parses each response) reattaches a `functionCall` part's required
    signature when that same call is replayed into a later request."""
    system_text: str | None = None
    contents: list[dict[str, Any]] = []

    # id -> name for every tool call any assistant turn requested, needed
    # to serialize a later `Message(role="tool")` into a `functionResponse`
    # part, which Gemini keys by name, not by the id our own types use.
    call_names: dict[str, str] = {}
    for message in messages:
        for tool_call in message.tool_calls or []:
            call_names[tool_call.id] = tool_call.name

    for message in messages:
        if message.role == "system":
            if system_text is None:
                system_text = message.content
            continue

        if message.role == "tool":
            name = call_names.get(message.tool_call_id or "", message.tool_call_id or "")
            function_response = {"name": name, "response": {"result": message.content}}
            contents.append({"role": "user", "parts": [{"functionResponse": function_response}]})
            continue

        role = "model" if message.role == "assistant" else "user"
        parts: list[dict[str, Any]] = []
        if message.content:
            parts.append({"text": message.content})
        for tool_call in message.tool_calls or []:
            part: dict[str, Any] = {
                "functionCall": {"name": tool_call.name, "args": tool_call.arguments}
            }
            signature = thought_signatures.get(tool_call.id)
            if signature is not None:
                part["thoughtSignature"] = signature
            parts.append(part)
        if not parts:
            parts.append({"text": ""})
        contents.append({"role": role, "parts": parts})

    return system_text, contents


def _sanitize_parameters_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Gemini's function-parameter schema is a restricted OpenAPI 3.0
    subset, not full JSON Schema -- found via a real `400 Bad Request`
    from a live sweep against AgentDojo's actual workspace tools, not
    assumed up front: `$defs`/`$ref` (Pydantic's `model_json_schema()`
    emits these for any nested model, e.g. `send_email`'s `attachments`
    parameter) and `additionalProperties` are all rejected outright
    ("Unknown name ...: Cannot find field", confirmed in the real error
    body). Resolves every `$ref` against the schema's own top-level
    `$defs` (recursively, since one `$defs` entry can reference another),
    then strips both `$defs` and every `additionalProperties` key from
    the result -- Gemini never complained about anything else in the same
    real schema (`title`, `default`, `anyOf`, nullable unions all passed
    through untouched), so nothing else is touched here."""
    defs = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                ref_name = node["$ref"].rsplit("/", 1)[-1]
                return resolve(defs.get(ref_name, {}))
            return {
                key: resolve(value)
                for key, value in node.items()
                if key not in ("$defs", "additionalProperties")
            }
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)


def _build_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Re-shapes `ModelRequest.tools`'s OpenAI-style
    `[{"type": "function", "function": {name, description, parameters}}, ...]`
    into Gemini's single-object-with-an-array shape:
    `[{"functionDeclarations": [{name, description, parameters}, ...]}]`,
    sanitizing each function's `parameters` schema along the way (see
    `_sanitize_parameters_schema`)."""
    if not tools:
        return None
    declarations = []
    for tool in tools:
        declaration = dict(tool["function"])
        if "parameters" in declaration:
            declaration["parameters"] = _sanitize_parameters_schema(declaration["parameters"])
        declarations.append(declaration)
    return [{"functionDeclarations": declarations}]


class GoogleAIStudioClient:
    """Hosted inference via Google AI Studio's Gemini REST API. Covers tier
    L4 -- confirmed real, tool-calling-capable via a live request before
    being wired into any sweep (S6-05's own verification discipline,
    mirroring how L5's Groq model was confirmed after its predecessor was
    silently retired -- see `configs/models.yaml`'s L5 comment)."""

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        session: requests.Session | None = None,
        timeout_s: float = 300.0,
    ) -> None:
        self.model = model
        self.cache_model_id = f"google_ai_studio:{model}"
        self.api_key = api_key or os.environ["GOOGLE_AI_STUDIO_API_KEY"]
        self.base_url = base_url.rstrip("/")
        self._session = session or requests.Session()
        self._timeout_s = timeout_s
        # tool-call id -> thoughtSignature, accumulated across every
        # `generate()` call this instance makes. One client is reused for
        # every turn of an episode (`sweep/runner.py`), so a signature
        # captured on the turn a call was requested is still available
        # when that same call is replayed into a later turn's history.
        self._thought_signatures: dict[str, str] = {}

    def generate(self, request: ModelRequest) -> ModelResponse:
        system_text, contents = _build_contents(request.messages, self._thought_signatures)

        payload: dict[str, Any] = {"contents": contents}
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        tools = _build_tools(request.tools)
        if tools:
            payload["tools"] = tools

        generation_config: dict[str, Any] = dict(request.params)
        if request.seed is not None:
            generation_config["seed"] = request.seed
        if generation_config:
            payload["generationConfig"] = generation_config

        start = time.perf_counter()
        response = self._session.post(
            f"{self.base_url}/models/{self.model}:generateContent",
            json=payload,
            params={"key": self.api_key},
            timeout=self._timeout_s,
        )
        response.raise_for_status()
        wall_ms = int((time.perf_counter() - start) * 1000)

        data = response.json()
        candidate_parts = data["candidates"][0]["content"].get("parts", [])
        text_parts = [p["text"] for p in candidate_parts if "text" in p]
        tool_calls = []
        for p in candidate_parts:
            if "functionCall" not in p:
                continue
            call_id = p["functionCall"].get("id") or str(uuid.uuid4())
            tool_calls.append(
                ToolCall(
                    id=call_id,
                    name=p["functionCall"]["name"],
                    arguments=p["functionCall"].get("args") or {},
                )
            )
            if "thoughtSignature" in p:
                self._thought_signatures[call_id] = p["thoughtSignature"]

        usage = data.get("usageMetadata", {})
        tokens_in = int(usage.get("promptTokenCount", 0))
        tokens_out = int(usage.get("candidatesTokenCount", 0))
        cost = compute_cost(
            provider="google_ai_studio",
            model=self.model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )

        return ModelResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            wall_ms=wall_ms,
            cost=cost,
            raw=data,
        )
