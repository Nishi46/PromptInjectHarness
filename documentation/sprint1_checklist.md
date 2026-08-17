# Sprint 1 Checklist — Harness Core (E1, E2 partial)

Granular sub-steps for each task in Sprint 1 of [sprint_planning.md](sprint_planning.md). Check items off as you go; each produces a concrete artifact (a file, a passing test, a committed config). Ordered to respect the `Deps` column in the sprint table — work top to bottom.

---

## S1-01 — Repo scaffold: pyproject.toml, package layout, ruff + mypy, pytest, CI (2h) 🔴

- [x] Decide the package name (`injection_pareto`, per the Sprint 1 acceptance criterion's CLI command) and create a `src/injection_pareto/` layout with `__init__.py`.
- [x] Write `pyproject.toml`: project metadata, Python version floor, and `agentdojo==0.1.35` pinned exactly (per the Sprint 1 risk note and S0-04's recorded version — never let this float).
- [x] Add dev dependencies to `pyproject.toml`: `ruff`, `mypy`, `pytest`.
- [x] Configure ruff (lint + format rules) under `[tool.ruff]` in `pyproject.toml`.
- [x] Configure mypy under `[tool.mypy]` (enable at least `disallow_untyped_defs`).
- [x] Configure pytest under `[tool.pytest.ini_options]` (`testpaths = ["tests"]`).
- [x] Create `tests/` with one trivial smoke test (`test_import.py` — `import injection_pareto`) so CI has something to run from commit one.
- [x] Add/confirm `.gitignore` covers `.venv`, `__pycache__`, `.env`, and a future trace-DB/cache directory.
- [x] Write `.github/workflows/ci.yml`: checkout → setup-python → `pip install -e ".[dev]"` → `ruff check .` → `mypy .` → `pytest`.
- [x] Run all four checks locally (`ruff check .`, `mypy .`, `pytest`, editable install) and confirm green before pushing.
- [x] Push and confirm the GitHub Actions run is green on the remote.

## S1-02 — `Defense` protocol + `DefenseStack` (3h) 🔴

- [x] Define shared dataclasses/types module (`types.py`): `Message`, `ToolCall`, `ToolResult`, `DefenseContext`, and a `CostRecord`.
- [x] Decide and write down the hook contract: what each of the 4 hooks receives, what it may return (pass-through vs. mutated vs. a block/deny verdict), and whether a hook can short-circuit the episode — this decision gates S2-07's tool allowlist later, so get it explicit now.
- [x] Write the `Defense` `Protocol` (`defenses/base.py`) with `on_pre_generate`, `on_tool_result`, `on_pre_tool_call`, and `cost()` signatures plus one-line docstrings stating the contract from the previous step.
- [x] Implement `DefenseStack`: holds an ordered list of `Defense` instances; each of its own 4 methods iterates the stack in order, threading the (possibly mutated) value through, and stops early on a block verdict.
- [x] Implement `DefenseStack.cost()` summing every member defense's `cost()`.
- [x] Unit test: two stub defenses appending to a shared list prove hook call order matches stack order.
- [x] Unit test: `DefenseStack.cost()` sums correctly across 3+ stub defenses with nonzero costs.
- [x] Unit test: a stub defense that returns a block verdict on `on_pre_tool_call` prevents any later defense in the stack from seeing that call.

## S1-03 — Trace schema: SQLite tables (3h) 🔴

- [x] Sketch the schema on paper/in a comment first: `run`, `episode`, `step`, `tool_call`, `defense_event`, `cost_record` — columns, primary keys, and foreign keys (episode→run, step→episode, tool_call→step, defense_event→episode or step, cost_record→run/episode).
- [x] Decide the indices needed to answer "total $ and p95 latency per episode" efficiently (index `cost_record.episode_id`, `step.episode_id`).
- [x] Write `trace/schema.sql` (or equivalent SQLAlchemy models) with all 6 tables, types, and FK constraints, with `PRAGMA foreign_keys = ON` enforced on connect.
- [x] Write `trace/db.py`: `connect(path)`, `init_db(conn)` (idempotently creates tables if missing), and a context-manager wrapper for a connection.
- [x] Write one insert helper per table (`insert_run`, `insert_episode`, `insert_step`, `insert_tool_call`, `insert_defense_event`, `insert_cost_record`), each returning the new row id.
- [x] Write an "episode reconstruction" query/helper joining `step` + `tool_call` + `defense_event` for one `episode_id`, ordered by timestamp.
- [x] Manual sanity pass: init a temp DB, insert one fake run/episode/step/tool_call, run the reconstruction query, and eyeball the output before wiring anything real into it.

## S1-04 — Model client wrapper (2.5h) 🔴

- [x] Define `ModelRequest` (messages, tools, params, seed) and `ModelResponse` (text, tool_calls, tokens_in, tokens_out, wall_ms, raw) dataclasses.
- [x] Write a base `ModelClient` protocol/ABC exposing `generate(request) -> ModelResponse`.
- [x] Implement `OllamaClient` (covers L1–L3): calls the local Ollama HTTP API, times the call with `time.perf_counter`, and reads token counts from Ollama's response metadata.
- [x] Implement one hosted client (start with Groq, since its key is already verified per `configs/models.yaml`) to prove the interface generalizes across vendors.
- [x] Write a per-provider cost table (Groq/Gemini rates, published per-1M-token list price since that's how they're actually quoted; $0 for Ollama) and compute `$` inside the wrapper before returning `ModelResponse`. OpenRouter omitted for now — every L6 pick is explicitly a rotating `:free` model (Appendix A), so there's no stable model string to rate yet; `compute_cost` raises a clear error for any unmapped hosted model rather than silently returning `$0`.
- [x] Decide and implement the persistence boundary: `ModelClient.generate()` returns a `ModelResponse` carrying its own `CostRecord`, but the client never touches the trace DB — persisting to `cost_record` is the caller's job (the S1-07 adapter, which owns `run_id`/`episode_id`). Documented in `ModelClient`'s docstring.
- [x] Unit test with HTTP mocked (no real network, fake `requests.Session` injected via constructor): confirm `tokens_in`, `tokens_out`, `wall_ms`, and `$` populate correctly for both the Ollama and Groq client, tool-call parsing for both, and that an unmapped hosted model raises instead of silently costing `$0`.

## S1-05 — Response cache (2h) 🔴

- [x] Decide the cache key: canonical-JSON-serialize `(model name + digest, messages, params, seed)` and hash it — canonicalization first, so key order never breaks a cache hit. `ModelClient.cache_model_id` carries the model identity (folds in the Ollama digest when known, since tags get republished); `compute_cache_key` in `cache/store.py` does the canonical-JSON + SHA-256 hash.
- [x] Implement a disk-backed content-addressed store (`cache/store.py`): `get(key)` / `put(key, response)`, JSON (de)serializing `ModelResponse`, keyed to a file path under a `.cache/responses/` dir.
- [x] Wire the cache into `ModelClient.generate`: check cache before calling the provider; on hit, return the cached response with `$0` cost and `cache_hit=True`; on miss, call the provider, store the result, mark `cache_hit=False`. Implemented as `clients/cached.py`'s `CachedModelClient` — a wrapper that itself satisfies the `ModelClient` protocol, so it composes with any client (Ollama, Groq, future ones) without duplicating cache logic per provider.
- [x] Add a `--no-cache` flag/param threaded down into the client call path; decide and implement its exact semantics (skip read only, or skip read+write). Decided: skip both — documented in `CachedModelClient`'s docstring. A debug flag silently overwriting a previously cached, reproducible result would undermine the project's digest-pinned reproducibility story, so `--no-cache` never mutates the cache.
- [x] Unit test: identical request issued twice — mock the underlying provider call and assert it fires exactly once (second call is a cache hit).
- [x] Unit test: `--no-cache` forces a fresh provider call even when a matching cache entry already exists.

## S1-06 — Config system (2h) 🟡

- [x] Design the YAML shape for an experiment config: `models`, `defenses`, `suites`, `attacks`, run/output name.
- [x] Write `configs/smoke.yaml` as the first concrete instance — single model, `no_defense` only, one suite, benign-only — this is what the Sprint 1 acceptance-criterion command runs against. Model/suite pinned to the exact combo already verified working in S0-04 (`llama3.2:3b`, `workspace`).
- [x] Write `config/schema.py`: typed dataclasses (`ModelSpec`, `OutputConfig`, `ExperimentConfig`, `RunSpec`) mirroring the YAML shape, with a `ConfigError` raised with a clear, field-naming message on anything malformed.
- [x] Write `config/loader.py`: `load_config(path) -> ExperimentConfig`, and `expand_run_specs(config) -> list[RunSpec]` expanding the `models × defenses × suites × attacks` cartesian product into a flat list of run specs (full parallel execution is S2-10's job; this just produces the list).
- [x] Wire the `python -m injection_pareto run <config.yaml>` CLI entrypoint that loads the config, expands and prints every run spec, then raises a clear `NotImplementedError` naming S1-07 — actually executing a run spec needs the AgentDojo adapter, which doesn't exist yet.
- [x] Unit test: loading `configs/smoke.yaml` produces the expected `ExperimentConfig`.
- [x] Unit test: a config missing a required field raises a clear, typed validation error (not a raw KeyError/AttributeError). Covered for three cases: missing top-level field, empty `models` list, and a model entry missing a required field.

## S1-07 — AgentDojo integration adapter (3h) 🔴

- [x] Re-read the "Integration notes (Sprint 0 install — S0-04)" section already written in [`docs/notes/agentdojo.md`](../docs/notes/agentdojo.md) for the concrete API surface before writing any adapter code.
- [x] Identify the exact AgentDojo entry points to wrap. Read `agentdojo`'s actual installed source (not just docs) to find them: `TaskSuite.run_task_with_pipeline(pipeline, user_task, injection_task, injections, runtime_class=...)` runs one episode without their CLI; `BasePipelineElement`/`AgentPipeline`/`ToolsExecutionLoop` are AgentDojo's own composable-hook architecture (their built-in PI-detector "defense" is just a pipeline element inserted into the loop — same shape as our `DefenseStack`); `FunctionsRuntime.run_function` is where every tool call actually executes, and `run_task_with_pipeline` takes a `runtime_class` *class* (not instance) — the clean seam for blocking a call before it runs, since AgentDojo has no built-in per-call hook for that. `Logger`/`TraceLogger` (contextvar-stack based) is AgentDojo's own supported way to observe the final message transcript without forking their retry/scoring logic.
- [x] Write `adapters/agentdojo_adapter.py`: `run_episode(*, conn, run_id, suite_name, user_task_id, injection_task_id, model_client, defense_stack, defense_name, provider, model_name, attack_name=None)` drives one episode. Beyond the checklist's core 5 params, it also takes `conn`/`run_id` (to satisfy the very next bullet — writing the trace as it runs) and `defense_name`/`provider`/`model_name` (human-readable labels needed for `blocked_by_defense`, `defense_event.defense_name`, and AgentDojo's own attack-loader, which resolves the "victim model" by substring-matching `pipeline.name`).
- [x] Map AgentDojo's tool-call/tool-result lifecycle onto the 4 `Defense` hooks. `on_pre_generate` → a custom `_PreGenerateElement` (`BasePipelineElement`) inserted before every LLM call; content-level message mutation only (matches the planned Sprint 2 defenses — spotlighting, instructional hardening — both text-level; adding/removing messages raises `NotImplementedError` rather than silently misbehaving). `on_pre_tool_call` + `on_tool_result` → both live in one `_make_defended_runtime_class` closure-based `FunctionsRuntime` subclass overriding `run_function`: a `BLOCK` verdict returns AgentDojo's own `("", "error message")` convention *before* the real function ever runs; the real result is stringified with AgentDojo's own `tool_result_to_str` (so the defense sees exactly what the model will) and can be mutated by `on_tool_result` before being returned.
- [x] Map AgentDojo's own utility/security scoring output into the episode record — `insert_episode(..., utility=utility, security=security)` from `run_task_with_pipeline`'s own return values directly; no reimplemented scoring.
- [x] Wire the adapter to write to the trace DB as the episode runs. In practice: accumulate everything (model responses, defense events, tool-call events) in memory via an `_EpisodeRecorder` during pipeline execution, then walk the final message transcript once and write steps/tool_calls/defense_events/cost_records in a single batched `transaction()` — matching the S1-03/S1-05 batching decision (commit-per-episode, not commit-per-row). Tool-call ↔ defense-event correlation exploits AgentDojo's guaranteed execution order (no ID matching needed): `ToolsExecutor` always runs `run_function` once per tool call in order, so the recorder's flat event list lines up positionally with the tool calls found in the post-hoc message walk.
- [x] Smoke test: built `NoDefense` (S1-09) and a `defenses/registry.py` (`resolve_defense`) since S1-07 needed a real defense to test against — both pulled forward from S1-09 rather than stubbed inline, since the real thing was one file. Ran the adapter live against `workspace`/`user_task_0` on `llama3.2:3b` via Ollama, benign and attacked (`important_instructions`, matching S0-04's exact target): **benign → `utility=False, security=True`; attacked → `utility=False, security=False`** — identical to S0-04's own direct-AgentDojo-CLI numbers for the same combo. `reconstruct_episode` confirmed a correct 4-step trace (system → user → assistant-with-tool-call → assistant-final-answer) with the tool call captured.

## S1-08 — Reproduce a published AgentDojo number (2.5h) 🔴

- [x] Pick the exact target: `llama3.2:3b` (L1), suite `workspace`, 6 tasks (`user_task_0/1/3/2/5/6`, first 6 by suite order), attack `important_instructions` + `injection_task_0`, defense `no_defense`. The AgentDojo *paper's* published table doesn't cover this model at all (it evaluates GPT-4/Claude/Gemini/a few large open-weight models, not a local 3B) — noted explicitly in `docs/reproduction.md` rather than faking a comparison that wouldn't validate anything.
- [x] Run that combo through the S1-07 adapter/runner end-to-end, capturing a full trace. `scripts/reproduce_s1_08.py` → `runs/local/reproduction/trace.db` (12 episodes: 6 benign + 6 attacked).
- [x] Run the identical combo through AgentDojo's own benchmark script directly (as in S0-04) in the current environment, to get an apples-to-apples reference number (model versions drift, so the paper's raw number alone isn't a fair comparison). Called their underlying `TaskSuite.run_task_with_pipeline` + `AgentPipeline.from_config` directly in the same script rather than shelling out to the CLI (same code path their CLI drives, no per-task subprocess/suite-reload overhead) — since the paper's number isn't comparable at all (previous bullet), *this* is the real apples-to-apples reference.
- [x] Query the trace DB for our computed ASR/utility and compare against both the paper's number and the direct-AgentDojo-run number; compute both deltas. Targeted ASR: 1.000 vs 1.000 (exact match). Benign/attacked utility: 0.333 vs 0.000 (+0.333 delta) — see next bullet.
- [x] If either delta is large or unexplained, debug the adapter (most likely culprit: scoring logic or prompt construction mismatch) before writing anything up as final. Root-caused, not an adapter bug: AgentDojo's own `LocalLLM` (`agent_pipeline/llms/local_llm.py`) uses a regex/text-delimiter tool-call parser built for generic local servers without native function-calling, and visibly failed to parse several of `llama3.2:3b`'s tool calls (`[debug] broken JSON: ...` printed during the run) — while our `OllamaClient` (S1-04) uses Ollama's *native* `tools=` API, which this model supports (`ollama list` confirms `"tools"` capability) and which reliably succeeds where AgentDojo's generic parser drops the call. Targeted ASR matching exactly is the signal that isolates this — it only needs the security scorer to see whether the attack succeeded, unaffected by tool-call-parsing format differences, and it agrees perfectly.
- [x] Write `docs/reproduction.md`: exact command run, our number, paper's number, direct-AgentDojo reference number, both deltas, and an explanation for any gap.

## S1-09 — `NoDefense` baseline (0.5h) 🔴

> Pulled forward into S1-07: the adapter's smoke test needed a real `Defense` to run against, and `NoDefense` was one small file — stubbing it inline would've cost about the same as just building it.

- [x] Implement `NoDefense` satisfying the `Defense` protocol: all 4 hooks are pure pass-throughs; `cost()` returns zero. `defenses/no_defense.py`.
- [x] Register it as the default/resolvable defense in the config schema and loader (e.g. an empty `defenses: []` or explicit `defenses: [no_defense]` both resolve to it). Config schema already stores defense as a plain string (`configs/smoke.yaml` uses `no_defense`); resolution is a separate small `defenses/registry.py` (`resolve_defense`) called at the adapter/call-site layer, not inside the loader itself — keeps the config schema decoupled from what defenses happen to be implemented yet.
- [x] Unit test: running `NoDefense` through `DefenseStack` produces output identical to bypassing the stack entirely — proves the interface adds no side effects. `tests/test_defenses_registry.py`.

## S1-10 — Unit tests for trace integrity + cost accounting (1.5h) 🟡

- [x] Test: insert a full episode (`run` → `episode` → `step`s → `tool_call`s → `defense_event`s → `cost_record`s) and confirm the S1-03 reconstruction query returns rows in correct chronological order. Also covers an episode-level defense event (`step_id=None`) landing in `episode_defense_events`, not just per-step ones.
- [x] Test: foreign-key constraints are enforced — inserting a `step` with a bogus `episode_id` fails (catches silent orphan rows). Covered for both `step` (orphan `episode_id`) and `tool_call` (orphan `step_id`).
- [x] Test: a hand-computed fixture dataset's total `$` and p95 latency per episode matches the SQL aggregation query's output — this directly proves the Sprint 1 "SQL query returns total $ and p95 latency per episode" acceptance criterion. Fixture of 4 cost records with hand-computed total `$` and a hand-computed linear-interpolation p95, checked against both `cost_summary_by_episode()` and a raw `SUM(usd) ... GROUP BY episode_id`-style SQL query directly.
- [x] Test: a cache-hit episode (S1-05) records `$0` in its `cost_record` — ties cache behavior to the trace schema. One episode with a real ($0.01) call followed by a cache-hit ($0, `cache_hit=1`) call for the same reasoning; both rows checked directly via SQL.
- [x] Confirm all of the above run automatically under the S1-01 CI workflow's `pytest` step — `tests/test_trace_integrity.py` needs no fixtures beyond `tmp_path`, so it runs the same as every other test under `pytest` in `.github/workflows/ci.yml`.

---

## Acceptance criteria (from sprint_planning.md)

- [ ] `python -m injection_pareto run configs/smoke.yaml` completes and writes a queryable trace DB — CLI loads/expands the config (S1-06) but doesn't execute yet; deliberately deferred to S2-10's sweep runner since the config schema has no per-task dimension to select what to run (see S1-06 notes). `scripts/reproduce_s1_08.py` proves the adapter itself does complete and write a queryable DB, just not through this exact command yet.
- [x] A SQL query returns total $ and p95 latency per episode — proven against the real `runs/local/reproduction/trace.db` in `docs/reproduction.md`'s "Cost and latency" section.
- [x] `docs/reproduction.md` shows your ASR vs. the paper's, with any gap explained — the paper doesn't cover this model at all (documented why), so the real comparison is ours vs. AgentDojo's own reference code on the identical model; gap root-caused to a tool-call-parsing difference, not an adapter bug.
- [x] Re-running an identical config with cache hits costs $0 — proven in S1-05's tests; also true in practice for `scripts/reproduce_s1_08.py` (shared `ResponseCache` across the run).

**Carry-forward risk:** AgentDojo's API is explicitly unstable — `agentdojo==0.1.35` must be pinned exactly in `pyproject.toml` at S1-01 and never floated (confirmed version recorded at S0-04).
