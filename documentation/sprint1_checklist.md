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

- [ ] Decide the cache key: canonical-JSON-serialize `(model name + digest, messages, params, seed)` and hash it — canonicalization first, so key order never breaks a cache hit.
- [ ] Implement a disk-backed content-addressed store (`cache/store.py`): `get(key)` / `put(key, response)`, JSON (de)serializing `ModelResponse`, keyed to a file path under a `.cache/responses/` dir.
- [ ] Wire the cache into `ModelClient.generate`: check cache before calling the provider; on hit, return the cached response with `$0` cost and `cache_hit=True`; on miss, call the provider, store the result, mark `cache_hit=False`.
- [ ] Add a `--no-cache` flag/param threaded down into the client call path; decide and implement its exact semantics (skip read only, or skip read+write).
- [ ] Unit test: identical request issued twice — mock the underlying provider call and assert it fires exactly once (second call is a cache hit).
- [ ] Unit test: `--no-cache` forces a fresh provider call even when a matching cache entry already exists.

## S1-06 — Config system (2h) 🟡

- [ ] Design the YAML shape for an experiment config: `models`, `defenses`, `suites`, `attacks`, run/output name.
- [ ] Write `configs/smoke.yaml` as the first concrete instance — single model, `NoDefense` only, one suite/task — this is what the Sprint 1 acceptance-criterion command runs against.
- [ ] Write `config/schema.py`: typed models (pydantic or dataclasses + manual validation) mirroring the YAML shape, with clear validation-error messages.
- [ ] Write `config/loader.py`: `load_config(path) -> ExperimentConfig`, expanding the `models × defenses × suites × attacks` cartesian product into a flat list of run specs (full parallel execution is S2-10's job; the loader just needs to produce the list).
- [ ] Wire the `python -m injection_pareto run <config.yaml>` CLI entrypoint that loads the config and executes the first run spec end-to-end.
- [ ] Unit test: loading `configs/smoke.yaml` produces the expected `ExperimentConfig`.
- [ ] Unit test: a config missing a required field raises a clear, typed validation error (not a raw KeyError/AttributeError).

## S1-07 — AgentDojo integration adapter (3h) 🔴

- [ ] Re-read the "Integration notes (Sprint 0 install — S0-04)" section already written in [`docs/notes/agentdojo.md`](../docs/notes/agentdojo.md) for the concrete API surface before writing any adapter code.
- [ ] Identify the exact AgentDojo entry points to wrap: programmatic suite loading, running a single `(user_task, injection_task | None)` pair without their CLI script, and where their pipeline exposes hooks around tool execution.
- [ ] Write `adapters/agentdojo_adapter.py`: a function/class taking `(suite_name, user_task_id, injection_task_id | None, model_client, defense_stack)` that drives one episode.
- [ ] Map AgentDojo's tool-call/tool-result lifecycle onto the 4 `Defense` hooks — `on_pre_generate` before each model turn, `on_pre_tool_call` before executing a tool, `on_tool_result` after a tool result comes back. This mapping is the core of the adapter; get it right before anything else here.
- [ ] Map AgentDojo's own utility/security scoring output into the episode record (utility bool, security bool).
- [ ] Wire the adapter to write to the trace DB as the episode runs: one `step` row per turn, one `tool_call` row per tool call, one `defense_event` row per hook firing, using the S1-03 insert helpers.
- [ ] Smoke test: run the adapter against one AgentDojo task with `NoDefense` (build the S1-09 stub first, or a trivial inline no-op if sequencing demands it) and confirm the resulting trace matches AgentDojo's own reported result for that task.

## S1-08 — Reproduce a published AgentDojo number (2.5h) 🔴

- [ ] Pick the exact target: one model + one attack combo with a published number (note suite, task subset, attack name, model — precisely).
- [ ] Run that combo through the S1-07 adapter/runner end-to-end, capturing a full trace.
- [ ] Run the identical combo through AgentDojo's own benchmark script directly (as in S0-04) in the current environment, to get an apples-to-apples reference number (model versions drift, so the paper's raw number alone isn't a fair comparison).
- [ ] Query the trace DB for our computed ASR/utility and compare against both the paper's number and the direct-AgentDojo-run number; compute both deltas.
- [ ] If either delta is large or unexplained, debug the adapter (most likely culprit: scoring logic or prompt construction mismatch) before writing anything up as final.
- [ ] Write `docs/reproduction.md`: exact command run, our number, paper's number, direct-AgentDojo reference number, both deltas, and an explanation for any gap.

## S1-09 — `NoDefense` baseline (0.5h) 🔴

- [ ] Implement `NoDefense` satisfying the `Defense` protocol: all 4 hooks are pure pass-throughs; `cost()` returns zero.
- [ ] Register it as the default/resolvable defense in the config schema and loader (e.g. an empty `defenses: []` or explicit `defenses: [no_defense]` both resolve to it).
- [ ] Unit test: running `NoDefense` through `DefenseStack` produces output identical to bypassing the stack entirely — proves the interface adds no side effects.

## S1-10 — Unit tests for trace integrity + cost accounting (1.5h) 🟡

- [ ] Test: insert a full episode (`run` → `episode` → `step`s → `tool_call`s → `defense_event`s → `cost_record`s) and confirm the S1-03 reconstruction query returns rows in correct chronological order.
- [ ] Test: foreign-key constraints are enforced — inserting a `step` with a bogus `episode_id` fails (catches silent orphan rows).
- [ ] Test: a hand-computed fixture dataset's total `$` and p95 latency per episode matches the SQL aggregation query's output — this directly proves the Sprint 1 "SQL query returns total $ and p95 latency per episode" acceptance criterion.
- [ ] Test: a cache-hit episode (S1-05) records `$0` in its `cost_record` — ties cache behavior to the trace schema.
- [ ] Confirm all of the above run automatically under the S1-01 CI workflow's `pytest` step.

---

## Acceptance criteria (from sprint_planning.md)

- [ ] `python -m injection_pareto run configs/smoke.yaml` completes and writes a queryable trace DB
- [ ] A SQL query returns total $ and p95 latency per episode
- [ ] `docs/reproduction.md` shows your ASR vs. the paper's, with any gap explained
- [ ] Re-running an identical config with cache hits costs $0

**Carry-forward risk:** AgentDojo's API is explicitly unstable — `agentdojo==0.1.35` must be pinned exactly in `pyproject.toml` at S1-01 and never floated (confirmed version recorded at S0-04).
