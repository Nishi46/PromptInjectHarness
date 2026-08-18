# MCP suite — architecture decision (S3-01)

**Decision: a parallel, AgentDojo-independent execution path.**

Every existing execution path — `adapters/agentdojo_adapter.py::run_episode`,
`scoring/security.py`, the attack registry (`attacks/registry.py`) — is
hard-wired to AgentDojo's own `TaskSuite` abstraction: `get_suite()`,
`suite.run_task_with_pipeline()`, `suite.tools`, `BaseInjectionTask.ground_truth()`.
None of that exists for a hand-authored MCP suite, and forcing it to fit
would fight the sprint's own premise: the injection lives in a *tool
schema* AgentDojo's task suites don't model at all (there is no "poisoned
tool description" concept anywhere in AgentDojo's data model).

## Reused as-is (suite-agnostic already)

- `Defense` protocol + `DefenseStack` (`defenses/base.py`, `defenses/stack.py`)
  — the 4-hook contract has no AgentDojo dependency.
- `ModelClient` / `CachedModelClient` (`clients/base.py`, `clients/cached.py`)
  — `generate(request)` only needs `messages` + an OpenAI-style `tools` list,
  which `mcp.runtime.MockMCPRuntime.tool_schema()` produces directly.
- `ResponseCache` (`cache/store.py`).
- The trace schema (`trace/schema.sql`) and its insert helpers
  (`trace/db.py`) — `run.suite`, `episode.task_id`, `episode.injection_task_id`
  are plain `TEXT`/nullable columns with no FK or format tied to AgentDojo.
- `scoring/utility.py::benign_utility_rate` and its injection-free assertion
  — operates on `episode` rows by `(defense, model)`, no suite-specific logic.
- `ToolAllowlist` (S2-07) — its recipient/cc/bcc argument check only needs
  `DefenseContext.metadata["user_task_prompt"]`, which any adapter can set.

## New, MCP-specific (built starting S3-01)

- `mcp/types.py` — `MockServer`/`MockTool`/`ResponseVariant`: the static
  spec model. No execution logic.
- `mcp/loader.py` — YAML spec → `MockServer`, mirroring `config/loader.py`'s
  `load_config` style (`yaml.safe_load` + a typed `_from_dict` walk, errors
  as `MCPSpecError` naming the bad field).
- `mcp/runtime.py::MockMCPRuntime` — the suite's `FunctionsRuntime`
  equivalent: mounts one or more `MockServer`s, exposes their combined
  `tool_schema()`, dispatches `call_tool()`. Every dispatch runs under a
  `socket.socket`-patching guard (`MCPNetworkError`), enforced at the
  runtime level so no individual server author has to remember it.
- `MockServerState` (also in `mcp/runtime.py`) — the stateful escape hatch
  for servers whose responses depend on prior calls (list-after-create),
  loaded from a `stateful: "module:ClassName"` spec field. Most servers
  won't need this; `MockTool.resolve`'s static per-argument `variants`
  covers everything else.

Deferred to later S3 tasks, not yet built:

- **S3-02** — 15 real server specs under `mcp/servers/`.
- **S3-03** — `mcp/tasks.py` (benign multi-tool tasks + a generic
  `check_completion` matcher).
- **S3-04** — `mcp/poisoned.py::PoisonedCase`, built on top of
  `MockServer.with_tool_description()` (already in S3-01 — the mutation
  seam a poisoned case applies through, returning a new server rather than
  editing one in place).
- **S3-05** — `mcp/injection_tasks.py` + `mcp/scoring.py`, mirroring
  S2-08's three-state `partial_compromise` model exactly.
- **S3-06** — `adapters/mcp_adapter.py::run_mcp_episode`: the actual
  integration point. It needs its own minimal multi-turn loop (there is no
  AgentDojo pipeline to drive here) but writes to the trace DB through the
  same `trace/db.py` insert helpers, and threads tool calls through the
  same `Defense.on_pre_tool_call`/`on_tool_result` hooks `run_episode`
  already uses. `sweep/runner.py` needs a small dispatch (`point.spec.suite
  == "mcp"` → `run_mcp_episode_fn` instead of `run_episode_fn`) — the
  resumability/concurrency machinery (`_pending_points`, `_find_or_create_run`)
  needs no changes, since it already operates on suite-agnostic columns.

## Open question for S3-06

Whether `adapters/agentdojo_adapter.py::_write_episode_trace` can be
parameterized to drop its AgentDojo-specific bits (`valid_function_names`,
`ChatMessage`-shaped message walking) and serve both adapters, or whether a
thin `_write_mcp_episode_trace` fork is cleaner. Not decided here — revisit
when `run_mcp_episode` is actually written, once its message/trace shape is
concrete.
