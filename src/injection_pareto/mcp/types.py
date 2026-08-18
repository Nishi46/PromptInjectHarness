from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any


class MCPSpecError(ValueError):
    """A malformed mock-server spec or an invalid runtime configuration built
    from one. The message names the bad field/file, mirroring
    `config.schema.ConfigError`'s style."""


@dataclass
class ResponseVariant:
    """One argument-matched override on a `MockTool`'s default response.
    `when` is matched by exact equality on every key it names (e.g.
    `{"path": "secret.txt"}` only matches a call whose `path` argument is
    exactly `"secret.txt"`) -- the first variant whose `when` matches the
    call's arguments wins; declaration order is the tie-break."""

    when: dict[str, Any]
    response: Any = None
    error: str | None = None

    def matches(self, arguments: dict[str, Any]) -> bool:
        return all(arguments.get(key) == value for key, value in self.when.items())


@dataclass
class MockTool:
    """One tool a `MockServer` exposes. `description` is the field S3-04's
    poisoned-description cases mutate -- kept as a plain string, not
    templated, so a mutation is a straightforward string replacement."""

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    default_response: Any = None
    default_error: str | None = None
    variants: list[ResponseVariant] = field(default_factory=list)

    def resolve(self, arguments: dict[str, Any]) -> tuple[Any, str | None]:
        """Returns `(response_value, error)` for a call with these arguments.
        `error is not None` means the call should be reported as a tool
        error, not a successful result."""
        for variant in self.variants:
            if variant.matches(arguments):
                return variant.response, variant.error
        return self.default_response, self.default_error


@dataclass
class MockServer:
    """A declared MCP-style server: a name plus the tools it exposes. Fully
    static and sandboxed by construction -- there is no code here that can
    reach the network; `stateful`, if set, names a `module:ClassName`
    handler (`runtime.MockServerState`) that `MockMCPRuntime` loads and runs
    under the same no-network guard as everything else."""

    name: str
    description: str = ""
    tools: list[MockTool] = field(default_factory=list)
    stateful: str | None = None

    def get_tool(self, name: str) -> MockTool | None:
        return next((t for t in self.tools if t.name == name), None)

    def with_tool_description(self, tool_name: str, description: str) -> MockServer:
        """Returns a copy of this server with one tool's `description`
        replaced -- every other field of every tool (including that same
        tool's `parameters`/responses) is untouched. This is the seam a
        future `PoisonedCase.apply()` (S3-04) mutates through: a poisoned
        server is always derived from a clean one, never edited in place."""
        tool = self.get_tool(tool_name)
        if tool is None:
            raise MCPSpecError(
                f"server {self.name!r} has no tool named {tool_name!r} to mutate"
            )
        mutated_tool = dataclasses.replace(tool, description=description)
        new_tools = [mutated_tool if t.name == tool_name else t for t in self.tools]
        return dataclasses.replace(self, tools=new_tools)
