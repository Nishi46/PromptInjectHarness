"""Fixture for tests/test_mcp_runtime.py's stateful-escape-hatch test.
Referenced by the dotted-path spec `mcp_stateful_fixture:CounterState`
(module resolves as a top-level import since pytest puts `tests/` on
`sys.path`, same as every other test module here)."""

from __future__ import annotations

from typing import Any


class CounterState:
    """Minimal `MockServerState`: an `increment` tool whose result depends
    on how many times it's already been called this episode -- the
    canonical case the stateful escape hatch exists for (list-after-create
    style servers)."""

    def __init__(self) -> None:
        self._count = 0

    def handle(self, tool_name: str, arguments: dict[str, Any]) -> tuple[Any, str | None]:
        if tool_name != "increment":
            return None, f"unknown tool {tool_name!r} for stateful handler"
        self._count += 1
        return self._count, None
