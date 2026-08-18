from __future__ import annotations

import contextlib
import importlib
import json
import socket
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from injection_pareto.mcp.types import MCPSpecError, MockServer, MockTool
from injection_pareto.types import ToolResult


class MCPNetworkError(RuntimeError):
    """Raised if a mock server handler attempts to open a real socket. Mock
    servers must be fully sandboxed (Sprint 3 acceptance criterion) --
    this is enforced at the runtime level, not left to server-author
    discipline, so every server mounted through `MockMCPRuntime` is
    covered automatically."""


class MockServerState(Protocol):
    """The stateful escape hatch for a `MockServer` whose responses depend
    on prior calls (e.g. list-after-create in a ticketing or calendar
    server), instead of the static per-argument `ResponseVariant` matching
    `MockTool.resolve` already covers. Referenced from a server spec via
    `stateful: "module.path:ClassName"`; `MockMCPRuntime` instantiates one
    instance per mounted stateful server (no-arg constructor) and keeps it
    for the runtime's lifetime, so state persists across calls within one
    episode."""

    def handle(self, tool_name: str, arguments: dict[str, Any]) -> tuple[Any, str | None]:
        """Returns `(response_value, error)` -- same contract as
        `MockTool.resolve`; `error is not None` means the call failed."""
        ...


def _load_stateful_handler(spec: str) -> MockServerState:
    module_name, _, class_name = spec.partition(":")
    if not module_name or not class_name:
        raise MCPSpecError(
            f"invalid 'stateful' spec {spec!r} -- expected 'module.path:ClassName'"
        )
    module = importlib.import_module(module_name)
    handler_cls = getattr(module, class_name, None)
    if handler_cls is None:
        raise MCPSpecError(
            f"stateful handler class {class_name!r} not found in module {module_name!r}"
        )
    handler: MockServerState = handler_cls()
    return handler


@contextlib.contextmanager
def _guard_no_network() -> Iterator[None]:
    """Monkeypatches `socket.socket` for the duration of one tool dispatch
    so any handler -- canned-response lookup or a stateful class -- that
    tries to open a real connection raises `MCPNetworkError` instead of
    reaching the network."""
    original_socket = socket.socket

    def _blocked(*args: Any, **kwargs: Any) -> Any:
        raise MCPNetworkError(
            "a mock MCP server attempted to open a real socket -- mock servers must make no "
            "real network calls (Sprint 3 acceptance criterion)"
        )

    socket.socket = _blocked  # type: ignore[assignment, misc]
    try:
        yield
    finally:
        socket.socket = original_socket  # type: ignore[misc]


def _stringify(value: Any) -> str:
    """Tool results round-trip through `ToolResult.content: str` (matching
    AgentDojo's own `tool_result_to_str` convention in
    `adapters/agentdojo_adapter.py`) -- a plain string response passes
    through unchanged, anything else is JSON-encoded."""
    if isinstance(value, str):
        return value
    return json.dumps(value)


@dataclass
class MockMCPRuntime:
    """The `FunctionsRuntime` equivalent for the MCP suite: holds one or
    more mounted `MockServer`s, exposes their combined tool schema in the
    same `{"type": "function", "function": {...}}` shape
    `agentdojo_adapter._build_tool_schema` produces (so `ModelClient.generate`
    needs no changes to accept it), and dispatches a tool call to the
    server that declares it."""

    servers: list[MockServer]
    _by_tool: dict[str, tuple[MockServer, MockTool]] = field(init=False, default_factory=dict)
    _state_handlers: dict[str, MockServerState] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        for server in self.servers:
            for tool in server.tools:
                if tool.name in self._by_tool:
                    other = self._by_tool[tool.name][0]
                    raise MCPSpecError(
                        f"tool name {tool.name!r} is registered by more than one mounted server "
                        f"({other.name!r} and {server.name!r}) -- mount fewer servers together "
                        "or namespace tool names"
                    )
                self._by_tool[tool.name] = (server, tool)
            if server.stateful is not None:
                self._state_handlers[server.name] = _load_stateful_handler(server.stateful)

    def tool_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for _server, tool in self._by_tool.values()
        ]

    def call_tool(self, *, tool_call_id: str, name: str, arguments: dict[str, Any]) -> ToolResult:
        if name not in self._by_tool:
            return ToolResult(
                tool_call_id=tool_call_id, content=f"unknown tool {name!r}", is_error=True
            )

        server, tool = self._by_tool[name]
        with _guard_no_network():
            if server.name in self._state_handlers:
                response, error = self._state_handlers[server.name].handle(name, arguments)
            else:
                response, error = tool.resolve(arguments)

        if error is not None:
            return ToolResult(tool_call_id=tool_call_id, content=error, is_error=True)
        return ToolResult(tool_call_id=tool_call_id, content=_stringify(response), is_error=False)
