from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from agentdojo.agent_pipeline.agent_pipeline import AgentPipeline, load_system_message
from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.agent_pipeline.basic_elements import InitQuery, SystemMessage
from agentdojo.agent_pipeline.llms.google_llm import EMPTY_FUNCTION_NAME
from agentdojo.agent_pipeline.tool_execution import (
    ToolsExecutionLoop,
    ToolsExecutor,
    tool_result_to_str,
)
from agentdojo.attacks.attack_registry import load_attack
from agentdojo.functions_runtime import EmptyEnv, Env, FunctionCall, FunctionsRuntime
from agentdojo.logging import Logger
from agentdojo.task_suite.load_suites import get_suite
from agentdojo.types import ChatMessage, get_text_content_as_str, text_content_block_from_string

from injection_pareto.attacks.registry import resolve_attack_name
from injection_pareto.clients.base import ModelClient, ModelRequest, ModelResponse
from injection_pareto.defenses.stack import DefenseStack
from injection_pareto.trace.db import (
    insert_cost_record,
    insert_defense_event,
    insert_episode,
    insert_step,
    insert_tool_call,
    transaction,
)
from injection_pareto.types import DefenseContext, Message, ToolCall, ToolResult, Verdict

# AgentDojo's own CLI default (scripts/benchmark.py) — the latest, most complete
# suite revision. Pinned here for the same reason agentdojo itself is pinned in
# pyproject.toml: their API (including suite versioning) is explicitly unstable.
BENCHMARK_VERSION = "v1.2.2"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _agentdojo_messages_to_ours(messages: Sequence[ChatMessage]) -> list[Message]:
    """Flatten AgentDojo's richer `ChatMessage` union into our simplified
    `Message`. Round-trips tool-call structure (needed to feed a real
    multi-turn transcript back into `ModelClient.generate`); content is
    reduced to its text form."""
    converted: list[Message] = []
    for raw in messages:
        role = raw["role"]
        if role == "tool":
            content = get_text_content_as_str(raw.get("content") or [])
            converted.append(
                Message(role="tool", content=content, tool_call_id=raw.get("tool_call_id"))
            )
            continue

        content = get_text_content_as_str(raw.get("content") or [])
        tool_calls = None
        if role == "assistant" and raw.get("tool_calls"):
            tool_calls = [
                ToolCall(id=fc.id or "", name=fc.function, arguments=dict(fc.args))
                for fc in raw["tool_calls"]
            ]
        converted.append(Message(role=role, content=content, tool_calls=tool_calls))
    return converted


def _build_tool_schema(runtime: FunctionsRuntime) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": fn.name,
                "description": fn.description,
                "parameters": fn.parameters.model_json_schema(),
            },
        }
        for fn in runtime.functions.values()
    ]


def _response_to_assistant_message(response: ModelResponse) -> ChatMessage:
    content = [text_content_block_from_string(response.text)] if response.text else None
    tool_calls = [
        FunctionCall(id=tc.id, function=tc.name, args=tc.arguments) for tc in response.tool_calls
    ] or None
    return {"role": "assistant", "content": content, "tool_calls": tool_calls}


@dataclass
class _DefenseEventRecord:
    hook: str
    verdict: str
    reason: str | None
    timestamp: str


@dataclass
class _EpisodeRecorder:
    """Accumulates everything one episode produces, in memory, so it can be
    written to the trace DB in a single batched `transaction()` at the end
    rather than one commit per event (see S1-03/S1-05 on why that matters)."""

    model_responses: list[ModelResponse] = field(default_factory=list)
    pre_generate_events: list[_DefenseEventRecord] = field(default_factory=list)
    # One entry per tool call encountered, in the exact order `ToolsExecutor`
    # processes them — that strict ordering is what lets the post-hoc trace
    # pass correlate an event back to its tool call without needing a shared key.
    tool_call_events: list[list[_DefenseEventRecord]] = field(default_factory=list)

    def reset_for_new_attempt(self) -> None:
        """`TaskSuite.run_task_with_pipeline` retries the whole pipeline up
        to 3x internally on a malformed/empty final output, each time from a
        fresh empty transcript — so anything positionally correlated against
        the *final* transcript (defense events keyed to tool-call position)
        must be dropped when a new attempt starts, or a discarded attempt's
        events get attributed to the wrong tool calls in the kept attempt.
        `model_responses` is deliberately NOT cleared: those API calls really
        happened and really cost tokens/$, so their cost stays counted even
        though their content doesn't appear in the final trace."""
        self.pre_generate_events.clear()
        self.tool_call_events.clear()


class _ModelClientLLM(BasePipelineElement):
    """Bridges our `ModelClient` (cached, cost-tracked — S1-04/S1-05) into
    AgentDojo's pipeline as the LLM stage, instead of using AgentDojo's own
    LLM elements — that's the whole point of building S1-04/S1-05."""

    def __init__(self, client: ModelClient, recorder: _EpisodeRecorder) -> None:
        self.name = client.cache_model_id
        self._client = client
        self._recorder = recorder

    def query(
        self,
        query: str,
        runtime: FunctionsRuntime,
        env: Env = EmptyEnv(),
        messages: Sequence[ChatMessage] = [],
        extra_args: dict = {},
    ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict]:
        our_messages = _agentdojo_messages_to_ours(messages)
        tools = _build_tool_schema(runtime)
        response = self._client.generate(ModelRequest(messages=our_messages, tools=tools))
        self._recorder.model_responses.append(response)
        assistant_message = _response_to_assistant_message(response)
        return query, runtime, env, [*messages, assistant_message], extra_args


class _PreGenerateElement(BasePipelineElement):
    """Fires `Defense.on_pre_generate` before every LLM call. Only content-level
    mutation round-trips back onto the real messages (matching the planned
    Sprint 2 defenses — spotlighting, instructional hardening — which both
    operate on system/tool-output text, not message structure)."""

    def __init__(
        self, defense_stack: DefenseStack, context: DefenseContext, recorder: _EpisodeRecorder
    ) -> None:
        self._defense_stack = defense_stack
        self._context = context
        self._recorder = recorder

    def query(
        self,
        query: str,
        runtime: FunctionsRuntime,
        env: Env = EmptyEnv(),
        messages: Sequence[ChatMessage] = [],
        extra_args: dict = {},
    ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict]:
        if len(messages) == 2:
            # Exactly [system, user] — the state only ever seen at the very
            # top of a pipeline run (this element sits right after
            # SystemMessage+InitQuery in `run_episode`'s pipeline list), on
            # both the real first attempt and every internal AgentDojo retry.
            # Either way, nothing recorded so far belongs to this attempt.
            self._recorder.reset_for_new_attempt()

        our_messages = _agentdojo_messages_to_ours(messages)
        result = self._defense_stack.on_pre_generate(self._context, our_messages)
        self._recorder.pre_generate_events.append(
            _DefenseEventRecord(
                hook="on_pre_generate",
                verdict=result.verdict.value,
                reason=result.reason,
                timestamp=_now(),
            )
        )

        if result.value is our_messages:
            return query, runtime, env, messages, extra_args
        if len(result.value) != len(our_messages):
            raise NotImplementedError(
                "on_pre_generate returned a different number of messages; this adapter only "
                "supports content-level mutation, not adding/removing messages."
            )

        mutated = list(messages)
        for i, (before, after) in enumerate(zip(our_messages, result.value, strict=True)):
            if after.content != before.content:
                new_content = [text_content_block_from_string(after.content)]
                mutated[i] = {**mutated[i], "content": new_content}
        return query, runtime, env, mutated, extra_args


def _make_defended_runtime_class(
    defense_stack: DefenseStack, context: DefenseContext, recorder: _EpisodeRecorder
) -> type[FunctionsRuntime]:
    """`suite.run_task_with_pipeline` instantiates `runtime_class(self.tools)`
    itself (no room to pass extra constructor args), so `on_pre_tool_call`/
    `on_tool_result` are wired in via a runtime subclass closed over this
    episode's defense stack — not via pipeline elements, since blocking a
    call cleanly means never reaching `FunctionsRuntime.run_function` at all."""

    class DefendedFunctionsRuntime(FunctionsRuntime):
        def run_function(
            self,
            env: object,
            function: str,
            kwargs: object,
            raise_on_error: bool = False,
        ) -> tuple[object, str | None]:
            tool_call = ToolCall(id=str(uuid.uuid4()), name=function, arguments=dict(kwargs))  # type: ignore[call-overload]
            pre_result = defense_stack.on_pre_tool_call(context, tool_call)
            events = [
                _DefenseEventRecord(
                    hook="on_pre_tool_call",
                    verdict=pre_result.verdict.value,
                    reason=pre_result.reason,
                    timestamp=_now(),
                )
            ]

            if pre_result.verdict is Verdict.BLOCK:
                recorder.tool_call_events.append(events)
                if raise_on_error:
                    raise PermissionError(f"Blocked by defense: {pre_result.reason}")
                return "", f"Blocked by defense: {pre_result.reason}"

            called = pre_result.value
            raw_result, error = super().run_function(
                env, called.name, called.arguments, raise_on_error
            )
            content = tool_result_to_str(raw_result) if error is None else error

            tool_result = ToolResult(
                tool_call_id=tool_call.id, content=content, is_error=error is not None
            )
            post_result = defense_stack.on_tool_result(context, tool_result)
            events.append(
                _DefenseEventRecord(
                    hook="on_tool_result",
                    verdict=post_result.verdict.value,
                    reason=post_result.reason,
                    timestamp=_now(),
                )
            )
            recorder.tool_call_events.append(events)

            return post_result.value.content, error

    return DefendedFunctionsRuntime


class _CapturingLogger(Logger):
    """AgentDojo's own extension point for observing a run's message
    transcript (see `TraceLogger`, which this mirrors) — used instead of
    reimplementing `run_task_with_pipeline`'s retry/scoring logic ourselves,
    which would risk silently drifting from the numbers it reports."""

    def __enter__(self) -> _CapturingLogger:
        self.messages: list[ChatMessage] = []
        self.logdir = None
        return super().__enter__()

    def log(self, messages: list[ChatMessage], **kwargs: object) -> None:
        self.messages = list(messages)

    def log_error(self, *args: object, **kwargs: object) -> None:
        pass


@dataclass
class EpisodeResult:
    episode_id: int
    utility: bool
    security: bool
    step_count: int
    tool_call_count: int


def run_episode(
    *,
    conn: sqlite3.Connection,
    run_id: int,
    suite_name: str,
    user_task_id: str,
    injection_task_id: str | None,
    model_client: ModelClient,
    defense_stack: DefenseStack,
    defense_name: str,
    provider: str,
    model_name: str,
    attack_name: str | None = None,
    benchmark_version: str = BENCHMARK_VERSION,
) -> EpisodeResult:
    """Drives one AgentDojo episode — `(suite_name, user_task_id,
    injection_task_id | None)` against `model_client` through `defense_stack`
    — and writes the full trace (episode/step/tool_call/defense_event/
    cost_record) to `conn` under `run_id`."""
    suite = get_suite(benchmark_version, suite_name)
    user_task = suite.get_user_task_by_id(user_task_id)

    recorder = _EpisodeRecorder()
    # `user_task_prompt` lets a defense (e.g. S2-07's `ToolAllowlist`) check a
    # tool-call argument against what the user actually asked for, without
    # that defense needing its own copy of the suite/task lookup.
    context = DefenseContext(
        task_id=user_task_id, metadata={"suite": suite_name, "user_task_prompt": user_task.PROMPT}
    )

    pre_generate = _PreGenerateElement(defense_stack, context, recorder)
    llm = _ModelClientLLM(model_client, recorder)
    tools_loop = ToolsExecutionLoop([ToolsExecutor(), pre_generate, llm])
    pipeline = AgentPipeline(
        [SystemMessage(load_system_message(None)), InitQuery(), pre_generate, llm, tools_loop]
    )
    # AgentDojo's attack loader resolves the victim model's name by substring
    # match against `pipeline.name` (`agentdojo.models.MODEL_NAMES`), with a
    # catch-all "local" entry — matching that keeps attacks like
    # `important_instructions` working for any Ollama-backed model.
    name_prefix = "local" if provider == "ollama" else provider
    pipeline.name = f"{name_prefix}-{model_name}-{defense_name}"

    injection_task = None
    injections: dict[str, str] = {}
    if injection_task_id is not None:
        if attack_name is None:
            raise ValueError("attack_name is required when injection_task_id is set")
        injection_task = suite.get_injection_task_by_id(injection_task_id)
        attack = load_attack(resolve_attack_name(attack_name), suite, pipeline)
        injections = attack.attack(user_task, injection_task)

    runtime_class = _make_defended_runtime_class(defense_stack, context, recorder)

    started_at = _now()
    with _CapturingLogger() as logger:
        utility, security = suite.run_task_with_pipeline(
            pipeline, user_task, injection_task, injections, runtime_class=runtime_class
        )
    messages = logger.messages
    ended_at = _now()

    episode_id = insert_episode(
        conn,
        run_id=run_id,
        task_id=user_task_id,
        injection_task_id=injection_task_id,
        started_at=started_at,
        ended_at=ended_at,
        utility=utility,
        security=security,
    )

    # ToolsExecutor rejects an empty/unregistered function name before ever
    # calling `FunctionsRuntime.run_function` (tool_execution.py) — those
    # calls never reach `recorder.tool_call_events`, so the walk below must
    # not consume a cursor slot for them.
    valid_function_names = {fn.name for fn in suite.tools}
    step_count, tool_call_count = _write_episode_trace(
        conn,
        run_id=run_id,
        episode_id=episode_id,
        messages=messages,
        recorder=recorder,
        defense_name=defense_name,
        model_name=model_name,
        valid_function_names=valid_function_names,
        defense_stack=defense_stack,
    )

    return EpisodeResult(
        episode_id=episode_id,
        utility=utility,
        security=security,
        step_count=step_count,
        tool_call_count=tool_call_count,
    )


def _write_episode_trace(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    episode_id: int,
    messages: Sequence[ChatMessage],
    recorder: _EpisodeRecorder,
    defense_name: str,
    model_name: str,
    valid_function_names: set[str],
    defense_stack: DefenseStack | None = None,
) -> tuple[int, int]:
    """Walk the final message transcript once and write every
    step/tool_call/defense_event/cost_record row in a single batched
    `transaction()`. Returns `(step_count, tool_call_count)`.

    Two things make positional correlation between `recorder.tool_call_events`
    and the tool calls found here non-trivial, both handled below:
      - `ToolsExecutionLoop` can hit its `max_iters` cutoff right after the
        LLM requests a batch of tool calls but before `ToolsExecutor` ever
        runs them — the transcript ends with an assistant message whose
        `tool_calls` have no corresponding result messages at all.
      - `ToolsExecutor` itself rejects an empty/unregistered function name
        before ever calling `FunctionsRuntime.run_function` — it still
        appends an error result message, but `recorder.tool_call_events` has
        no entry for that call, since our runtime never saw it.

    `defense_stack` is optional (defaults to `None`, skipping the block
    below) purely so existing low-level tests can call this function without
    constructing a live stack — every real call site (`run_episode`) passes
    one. A defense that makes its own model calls (e.g. S2-05's `GuardModel`)
    exposes them via a `responses: list[ModelResponse]` attribute; any
    defense with that attribute gets its calls written to `cost_record`
    labeled `defense:<defense_name>`, separable from the agent's own
    `model_name`-labeled rows (S2-05's "overhead is separable from base
    agent cost" acceptance criterion).
    """
    step_count = 0
    tool_call_count = 0
    with transaction(conn):
        tool_call_cursor = 0
        i = 0
        step_index = 0
        while i < len(messages):
            message = messages[i]
            if message["role"] == "tool":
                i += 1
                continue

            content = get_text_content_as_str(message.get("content") or [])
            step_id = insert_step(
                conn,
                episode_id=episode_id,
                step_index=step_index,
                role=message["role"],
                content=content,
                timestamp=_now(),
            )
            step_count += 1

            advance = 1
            if message["role"] == "assistant":
                tool_calls = message.get("tool_calls") or []
                result_messages: list[ChatMessage | None] = list(
                    messages[i + 1 : i + 1 + len(tool_calls)]
                )
                if len(result_messages) < len(tool_calls):
                    result_messages = [None] * len(tool_calls)

                for tc, result_message in zip(tool_calls, result_messages, strict=True):
                    reached_runtime = (
                        result_message is not None
                        and tc.function != EMPTY_FUNCTION_NAME
                        and tc.function in valid_function_names
                    )
                    events = recorder.tool_call_events[tool_call_cursor] if reached_runtime else []
                    if reached_runtime:
                        tool_call_cursor += 1
                    blocked = any(
                        e.hook == "on_pre_tool_call" and e.verdict == Verdict.BLOCK.value
                        for e in events
                    )
                    result_content = (
                        get_text_content_as_str(result_message.get("content") or [])
                        if result_message is not None
                        else None
                    )

                    result_json = json.dumps(result_content) if result_content is not None else None
                    tool_call_row_id = insert_tool_call(
                        conn,
                        step_id=step_id,
                        tool_name=tc.function,
                        arguments_json=json.dumps(tc.args),
                        result_json=result_json,
                        blocked_by_defense=defense_name if blocked else None,
                        timestamp=_now(),
                    )
                    tool_call_count += 1
                    for event in events:
                        detail = (
                            {"reason": event.reason, "tool_call_id": tool_call_row_id}
                            if event.reason
                            else None
                        )
                        insert_defense_event(
                            conn,
                            episode_id=episode_id,
                            step_id=step_id,
                            defense_name=defense_name,
                            hook=event.hook,
                            verdict=event.verdict,
                            detail_json=json.dumps(detail) if detail else None,
                            timestamp=event.timestamp,
                        )
                advance = 1 + len(tool_calls)

            i += advance
            step_index += 1

        for pg_event in recorder.pre_generate_events:
            insert_defense_event(
                conn,
                episode_id=episode_id,
                step_id=None,
                defense_name=defense_name,
                hook=pg_event.hook,
                verdict=pg_event.verdict,
                detail_json=json.dumps({"reason": pg_event.reason}) if pg_event.reason else None,
                timestamp=pg_event.timestamp,
            )

        for response in recorder.model_responses:
            insert_cost_record(
                conn,
                run_id=run_id,
                episode_id=episode_id,
                model=model_name,
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                wall_ms=response.wall_ms,
                usd=response.cost.usd,
                cache_hit=response.cache_hit,
                timestamp=_now(),
            )

        for defense in defense_stack.defenses if defense_stack is not None else []:
            for response in getattr(defense, "responses", []):
                insert_cost_record(
                    conn,
                    run_id=run_id,
                    episode_id=episode_id,
                    model=f"defense:{defense_name}",
                    tokens_in=response.tokens_in,
                    tokens_out=response.tokens_out,
                    wall_ms=response.wall_ms,
                    usd=response.cost.usd,
                    cache_hit=response.cache_hit,
                    timestamp=_now(),
                )

    return step_count, tool_call_count
