from __future__ import annotations

import re
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

# Candidate-value provenance heuristic: emails, URLs, and quoted substrings
# inside a tool result's text. Cheap regexes, not exhaustive value
# extraction -- a heuristic, not a classifier, same caveat
# `adaptive/mutator.py::_looks_in_family` states about its own family-drift
# check. A secret value in some other shape (a bare number, an unquoted
# multi-word phrase) won't be tagged and won't be caught; that's a known,
# documented blind spot, not chased with more special-casing here.
_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}")
_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")
_QUOTED_PATTERN = re.compile(r'"([^"\n]{1,200})"|\'([^\'\n]{1,200})\'')

_CANDIDATE_VALUE_PATTERNS = (_EMAIL_PATTERN, _URL_PATTERN, _QUOTED_PATTERN)


def _extract_candidate_values(text: str) -> set[str]:
    """Runs `_CANDIDATE_VALUE_PATTERNS` over `text`, returning every distinct
    email address, URL, and quoted substring found -- the provenance-tagging
    heuristic `CapabilityEnforcement.on_tool_result` feeds into its taint
    set."""
    values: set[str] = set()
    values.update(_EMAIL_PATTERN.findall(text))
    values.update(_URL_PATTERN.findall(text))
    for match in _QUOTED_PATTERN.finditer(text):
        quoted = match.group(1) if match.group(1) is not None else match.group(2)
        if quoted:
            values.add(quoted)
    return values


class CapabilityEnforcement:
    """D8 -- reduced CaMeL-style capability enforcement: provenance tagging
    (`on_tool_result`) plus a policy check on a small, documented set of
    high-risk "sink" tools per suite (`on_pre_tool_call`). The reduced scope
    is explicit and deliberate -- see `docs/notes/architectural_defenses.md`'s
    "Implementation decisions" section for why a literal CaMeL interpreter
    (a planner-emitted restricted program, a bespoke provenance-tracking
    executor) is out of scope for this sprint.

    Distinction from D6 (`ToolAllowlist`): D6 checks a fixed set of argument
    *names* (`recipient`, `to`, ...) against the user's own trusted prompt.
    D8 checks *any* string argument on a *sink tool* against values whose
    provenance is a tool result seen during the episode -- a value-provenance
    check, not a name-keyed one, so it can (and D6 structurally cannot) catch
    a secret leaking through a non-destination argument, e.g. a file's real
    contents echoed into a message body sent to an otherwise-legitimate-
    looking recipient.

    `sink_tools` is keyed by suite name (`"workspace"`, `"mcp"`), mirroring
    `ToolAllowlist.__init__`'s `task_allowlists` shape. Defaults to an empty
    policy (no suite configured -> no restriction anywhere) -- S5-04 wires
    in the real per-suite policy; this task's own tests exercise the
    mechanism against an injected fake policy, not a hardcoded one.
    """

    def __init__(self, *, sink_tools: Mapping[str, frozenset[str]] | None = None) -> None:
        self._sink_tools = sink_tools or {}
        # Every candidate value seen in any tool result this episode --
        # instance state, reset every episode for the same reason
        # `DualLLM.responses`/`GuardModel.responses` are (a fresh instance
        # per episode/round, per `resolve_defense`/S4-03's precedent).
        self._tainted_values: set[str] = set()

    def on_pre_generate(
        self, context: DefenseContext, messages: list[Message]
    ) -> HookResult[list[Message]]:
        return HookResult(value=messages)

    def on_tool_result(
        self, context: DefenseContext, tool_result: ToolResult
    ) -> HookResult[ToolResult]:
        self._tainted_values.update(_extract_candidate_values(tool_result.content))
        return HookResult(value=tool_result, verdict=Verdict.ALLOW)

    def on_pre_tool_call(
        self, context: DefenseContext, tool_call: ToolCall
    ) -> HookResult[ToolCall]:
        suite = context.metadata.get("suite")
        sink_args = self._sink_tools.get(suite) if isinstance(suite, str) else None
        if sink_args is None or tool_call.name not in sink_args:
            return HookResult(value=tool_call)

        user_task_prompt = context.metadata.get("user_task_prompt", "")
        for arg_name, arg_value in tool_call.arguments.items():
            # Mirrors `tool_allowlist.py`'s own value-scanning loop: an
            # argument's value is as often a list (e.g. a `to` list of
            # recipients) as a single string -- check every string element
            # either way.
            values = arg_value if isinstance(arg_value, list) else [arg_value]
            for value in values:
                if not isinstance(value, str):
                    continue
                # A sink argument that matters most here (a message body, a
                # file's contents) rarely *equals* a tainted value outright
                # -- it *carries* one inside other text (the D6-can't-catch-
                # this scenario D8 exists for, per this module's docstring).
                # So this checks substring containment both ways: does a
                # tainted value from some tool result appear inside this
                # argument, and does that same tainted value appear inside
                # the user's own trusted prompt (the escape hatch).
                for tainted in sorted(self._tainted_values):
                    if tainted and tainted in value and tainted not in user_task_prompt:
                        return HookResult(
                            value=tool_call,
                            verdict=Verdict.BLOCK,
                            reason=(
                                f"argument {arg_name!r} on tool {tool_call.name!r} contains "
                                f"{tainted!r}, a value first seen in a tool result during "
                                "this episode and absent from the user's original request "
                                "-- blocked by data-flow policy, not content classification"
                            ),
                        )

        return HookResult(value=tool_call)

    def cost(self) -> CostRecord:
        """Always empty: no model calls, pure regex extraction + set
        lookups, same as `ToolAllowlist.cost()`. This zero-cost property is
        itself a real, structurally-guaranteed result S5-06 reports (against
        `DualLLM`'s non-zero per-tool-call overhead) -- not an oversight or
        a placeholder."""
        return CostRecord()
