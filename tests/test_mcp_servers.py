"""Integration test over the real S3-02 server specs (not fixtures) -- a
cheap regression gate so a future edit to any `mcp/servers/*.yaml` can't
silently break loading, and so a name collision across servers is caught
here rather than discovered later when the sweep tries to mount them."""

from pathlib import Path

from injection_pareto.mcp.loader import load_server
from injection_pareto.mcp.runtime import MockMCPRuntime

SERVERS_DIR = Path(__file__).parent.parent / "src" / "injection_pareto" / "mcp" / "servers"

# `_example.yaml` (S3-01) is a throwaway fixture for tests/test_mcp_runtime.py,
# not one of the 15 real domains this sprint is scoped around.
REAL_SERVER_PATHS = sorted(p for p in SERVERS_DIR.glob("*.yaml") if p.stem != "_example")


def test_exactly_fifteen_real_servers_are_authored() -> None:
    assert len(REAL_SERVER_PATHS) == 15


def test_every_real_server_parses_and_has_at_least_one_tool() -> None:
    for path in REAL_SERVER_PATHS:
        server = load_server(path)
        assert server.name, f"{path}: server name is empty"
        assert server.tools, f"{path}: server has no tools"
        for tool in server.tools:
            assert tool.description.strip(), f"{path}: tool {tool.name!r} has an empty description"


def test_no_tool_name_is_reused_across_two_different_servers() -> None:
    seen: dict[str, str] = {}
    for path in REAL_SERVER_PATHS:
        server = load_server(path)
        for tool in server.tools:
            assert tool.name not in seen, (
                f"tool name {tool.name!r} is declared by both {seen.get(tool.name)!r} "
                f"and {server.name!r}"
            )
            seen[tool.name] = server.name


def test_all_fifteen_servers_mount_together_without_collision() -> None:
    servers = [load_server(path) for path in REAL_SERVER_PATHS]

    runtime = MockMCPRuntime(servers=servers)

    schema = runtime.tool_schema()
    tool_names = [entry["function"]["name"] for entry in schema]
    assert len(tool_names) == len(set(tool_names))
    assert len(tool_names) >= 30  # 15 servers x >=2 tools each
