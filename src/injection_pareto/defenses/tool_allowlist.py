from __future__ import annotations

from collections.abc import Mapping

from injection_pareto.types import (
    CostRecord,
    DefenseContext,
    HookResult,
    Message,
    ToolCall,
    ToolResult,
    Verdict,
)

# Argument *names* treated as destination/recipient fields, checked against
# the user's original task prompt. Suite-agnostic by design -- catches the
# textbook exfiltration/redirection pattern (an injected instruction routes
# a tool call's output or effect to an attacker-controlled destination the
# user never mentioned) across every AgentDojo suite's tools, without
# needing a hand-authored policy per task.
_DEFAULT_CONSTRAINED_ARG_NAMES = frozenset({"recipient", "recipients", "to", "cc", "bcc", "email"})


class ToolAllowlist:
    """D6 -- per-task tool allowlist + argument policy, both enforced on
    `on_pre_tool_call` (the only hook whose `BLOCK` verdict actually stops a
    call today -- see `Verdict` in `types.py`).

    Two independent checks:

    1. **Tool allowlist**: if `task_allowlists` has an entry for the
       episode's `context.task_id`, any call to a tool outside that set is
       blocked. Defaults to no restriction for any task (`task_allowlists`
       defaults to empty) -- authoring a comprehensive allowlist per
       AgentDojo task is a sweep-config-time decision (S2-11), not
       fabricated here; a caller can pass real policies in.
    2. **Argument policy**: any argument whose *name* is in
       `constrained_arg_names` (e.g. `recipient`, `to`) must have a string
       *value* that appears verbatim in the user's original task prompt --
       the sprint's own example ("recipient must appear in the user's
       original request"). Requires `context.metadata["user_task_prompt"]`
       (populated by the AgentDojo adapter); if it's absent, this check is
       skipped rather than blocking blind.

    `on_pre_generate` and `on_tool_result` are pure pass-throughs; `cost()`
    is zero (no model calls, pure policy checks).
    """

    def __init__(
        self,
        *,
        task_allowlists: Mapping[str, frozenset[str]] | None = None,
        constrained_arg_names: frozenset[str] = _DEFAULT_CONSTRAINED_ARG_NAMES,
    ) -> None:
        self._task_allowlists = task_allowlists or {}
        self._constrained_arg_names = {name.lower() for name in constrained_arg_names}

    def on_pre_generate(
        self, context: DefenseContext, messages: list[Message]
    ) -> HookResult[list[Message]]:
        return HookResult(value=messages)

    def on_pre_tool_call(
        self, context: DefenseContext, tool_call: ToolCall
    ) -> HookResult[ToolCall]:
        allowed_tools = self._task_allowlists.get(context.task_id) if context.task_id else None
        if allowed_tools is not None and tool_call.name not in allowed_tools:
            return HookResult(
                value=tool_call,
                verdict=Verdict.BLOCK,
                reason=f"tool {tool_call.name!r} is not in the allowlist for this task",
            )

        user_task_prompt = context.metadata.get("user_task_prompt")
        if isinstance(user_task_prompt, str):
            for arg_name, arg_value in tool_call.arguments.items():
                if arg_name.lower() not in self._constrained_arg_names:
                    continue
                # Recipient-style arguments are as often a list (e.g.
                # `send_email(recipients=[...])`) as a single string --
                # check every string element either way.
                values = arg_value if isinstance(arg_value, list) else [arg_value]
                for value in values:
                    if isinstance(value, str) and value not in user_task_prompt:
                        return HookResult(
                            value=tool_call,
                            verdict=Verdict.BLOCK,
                            reason=(
                                f"argument {arg_name!r} on tool {tool_call.name!r} contains "
                                f"{value!r}, which does not appear in the user's original request"
                            ),
                        )

        return HookResult(value=tool_call)

    def on_tool_result(
        self, context: DefenseContext, tool_result: ToolResult
    ) -> HookResult[ToolResult]:
        return HookResult(value=tool_result)

    def cost(self) -> CostRecord:
        return CostRecord()
