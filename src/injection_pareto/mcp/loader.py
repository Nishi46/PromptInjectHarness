from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from injection_pareto.mcp.types import MCPSpecError, MockServer, MockTool, ResponseVariant

# Where the S3-02 server specs actually live -- resolved relative to this
# package (not a test-only path derivation), so production code (S3-06's
# adapter) and tests both resolve the same directory.
SERVERS_DIR = Path(__file__).parent / "servers"


def load_named_server(name: str) -> MockServer:
    """Loads one of the real S3-02 servers by name (its `mcp/servers/<name>.yaml`
    file stem) -- the lookup `adapters/mcp_adapter.py::run_mcp_episode` uses
    to mount an `MCPUserTask`'s declared `servers`."""
    return load_server(SERVERS_DIR / f"{name}.yaml")


def load_server(path: str | Path) -> MockServer:
    """Parses one YAML mock-server spec into a `MockServer`. Raises
    `MCPSpecError` naming the bad field on anything malformed -- mirrors
    `config.loader.load_config`'s style (`yaml.safe_load` over the raw
    text, then a typed `from_dict`-shaped parse)."""
    path = Path(path)
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise MCPSpecError(f"{path}: server spec must be a YAML mapping at the top level")
    return _server_from_dict(raw, source=str(path))


def _server_from_dict(data: dict[str, Any], *, source: str) -> MockServer:
    missing = [k for k in ("name", "tools") if k not in data]
    if missing:
        raise MCPSpecError(
            f"{source}: server spec is missing required field(s): {', '.join(missing)}"
        )

    tools_data = data["tools"]
    if not isinstance(tools_data, list) or not tools_data:
        raise MCPSpecError(f"{source}: server.tools must be a non-empty list")

    tools = [_tool_from_dict(t, source=source, index=i) for i, t in enumerate(tools_data)]
    return MockServer(
        name=data["name"],
        description=data.get("description", ""),
        tools=tools,
        stateful=data.get("stateful"),
    )


def _tool_from_dict(data: dict[str, Any], *, source: str, index: int) -> MockTool:
    if not isinstance(data, dict):
        raise MCPSpecError(f"{source}: tools[{index}] must be a mapping")

    missing = [k for k in ("name", "description") if k not in data]
    if missing:
        raise MCPSpecError(
            f"{source}: tools[{index}] is missing required field(s): {', '.join(missing)}"
        )
    if not data["description"]:
        raise MCPSpecError(f"{source}: tools[{index}] ({data['name']!r}) has an empty description")

    variants_data = data.get("variants") or []
    if not isinstance(variants_data, list):
        raise MCPSpecError(f"{source}: tool {data['name']!r} variants must be a list")
    variants = [
        _variant_from_dict(v, source=source, tool_name=data["name"], index=i)
        for i, v in enumerate(variants_data)
    ]

    parameters = data.get("parameters") or {"type": "object", "properties": {}}
    if not isinstance(parameters, dict):
        raise MCPSpecError(f"{source}: tool {data['name']!r} parameters must be a mapping")

    return MockTool(
        name=data["name"],
        description=data["description"],
        parameters=parameters,
        default_response=data.get("response"),
        default_error=data.get("error"),
        variants=variants,
    )


def _variant_from_dict(
    data: dict[str, Any], *, source: str, tool_name: str, index: int
) -> ResponseVariant:
    if not isinstance(data, dict) or "when" not in data:
        raise MCPSpecError(
            f"{source}: tool {tool_name!r} variants[{index}] must be a mapping with a 'when' key"
        )
    when = data["when"]
    if not isinstance(when, dict) or not when:
        raise MCPSpecError(
            f"{source}: tool {tool_name!r} variants[{index}].when must be a non-empty mapping"
        )
    return ResponseVariant(when=dict(when), response=data.get("response"), error=data.get("error"))
