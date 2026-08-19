from __future__ import annotations

# S5-04's real per-suite sink-tool policy for `CapabilityEnforcement` (D8) --
# mirrors `tool_allowlist.py`'s `_DEFAULT_CONSTRAINED_ARG_NAMES` module-
# level-constant style. Picked per the sprint's own scope valve (2-3
# genuinely high-risk tools per suite, not an exhaustive policy):
#
#   workspace -> {"send_email", "share_file"}: the exact two tools
#     `tool_allowlist.py::_DEFAULT_CONSTRAINED_ARG_NAMES` was already
#     designed around (confirmed real tool names in AgentDojo's
#     `agentdojo/default_suites/v1/workspace/task_suite.py::TOOLS`) --
#     both take a destination argument plus at least one free-text
#     argument (`send_email`'s `body`, `share_file`'s `email`) a tainted
#     value could leak through.
#
#   mcp -> {"send_email", "create_payment", "post_message"}: confirmed real
#     tool names in `mcp/servers/email.yaml`, `mcp/servers/payments.yaml`,
#     `mcp/servers/messaging.yaml` respectively.
_DEFAULT_SINK_TOOLS_BY_SUITE: dict[str, frozenset[str]] = {
    "workspace": frozenset({"send_email", "share_file"}),
    "mcp": frozenset({"send_email", "create_payment", "post_message"}),
}
