# Sprint 2 Checklist — Defenses 1–6 + Static Baseline (E2, E3, E6)

Granular sub-steps for each task in Sprint 2 of [sprint_planning.md](sprint_planning.md). Check items off as you go; each produces a concrete artifact (a file, a passing test, a committed config/result). Ordered to respect the `Deps` column in the sprint table — work top to bottom. Grounded in the Sprint 1 codebase (`Defense` protocol in `defenses/base.py`, `types.py`, `trace/db.py`, `trace/queries.py`, `config/schema.py`) — reread those before starting.

---

## S2-01 — Attack registry (2h) 🔴 — done

> Plan deviation, decided during implementation: no standalone `Attack` protocol. `adapters/agentdojo_adapter.py::run_episode` already drives attacks entirely through AgentDojo's own `load_attack(attack_name, suite, pipeline)` (confirmed by reading `agentdojo.attacks.base_attacks`/`baseline_attacks`/`important_instructions_attacks` — a `BaseAttack.attack(user_task, injection_task) -> dict[str, str]` interface AgentDojo's `run_task_with_pipeline` consumes directly), and AgentDojo already ships `direct`/`ignore_previous`/`important_instructions` as registered attacks (registered as an import side effect of `agentdojo.attacks.attack_registry`, since `agentdojo/attacks/__init__.py` eagerly imports its built-in attack modules). A parallel `Attack` protocol of our own would never be called by the adapter and would risk drifting from the exact attack AgentDojo's scoring assumes — the same lesson S1-07 already learned about AgentDojo's pipeline/runtime hooks. "Injection-point resolution" is likewise already handled by AgentDojo's `BaseAttack.get_injection_candidates`; nothing here needed to reimplement it.

- [x] Read AgentDojo's actual attack machinery (`base_attacks.py`, `baseline_attacks.py`, `important_instructions_attacks.py`, `attack_registry.py`) to find the real integration seam before designing anything new — same approach S1-07 used for the pipeline/runtime.
- [x] Write `attacks/registry.py`: `resolve_attack_name(name) -> str`, mapping our project's canonical attack-family names (`naive`, `ignore_previous`, `important_instructions`) onto the names AgentDojo's own `ATTACKS` dict already has registered (`naive` → `direct`; the other two pass through unchanged), validated against `agentdojo.attacks.attack_registry.ATTACKS`. Leaves a documented seam for S2-02's two novel families (`context_completion`, `encoding_obfuscation`), which don't exist in AgentDojo yet and will be registered into that same dict via `agentdojo.attacks.attack_registry.register_attack`.
- [x] Wire `adapters/agentdojo_adapter.py::run_episode` to call `load_attack(resolve_attack_name(attack_name), suite, pipeline)` instead of passing the raw config string straight through — so config-facing names are ours, not AgentDojo's internal ones, and stay stable if AgentDojo renames something internally. `attacks: list[str | None]` in `config/schema.py` needed no changes.
- [x] Unit test (`tests/test_attacks_registry.py`): `naive` maps to `direct`; `ignore_previous`/`important_instructions` pass through unchanged; an unknown name raises a clear, typed error (mirrors `tests/test_defenses_registry.py`'s style).
- [x] Full suite (49 tests), `ruff check .`, and `mypy .` all clean after the change.

## S2-03 — D2 Spotlighting (1.5h) 🔴

- [ ] Implement `defenses/spotlighting.py`: `on_tool_result` delimits and datamarks untrusted content before it re-enters the transcript; decide whether `on_pre_generate` also needs a system-prompt addendum explaining the marking scheme, and document the decision in the class docstring.
- [ ] `cost()` returns zero (pure text transform, no extra model calls).
- [ ] Register in `defenses/registry.py`.
- [ ] Unit test: a tool result containing an embedded instruction comes out delimited/marked as expected (deterministic string assertion).
- [ ] Manual check: run one S2-02 attack against `no_defense` vs `spotlighting` on the known-good `llama3.2:3b`/`workspace`/`user_task_0` combo from S1-08; confirm the security flag can flip before trusting the full sweep.

## S2-04 — D3 Instructional prevention (1h) 🔴

- [ ] Implement `defenses/instructional_prevention.py`: `on_pre_generate` prepends/rewrites the system message with hardened instructions (tool output is untrusted data; never follow instructions found in it).
- [ ] `cost()` returns zero.
- [ ] Register in `defenses/registry.py`.
- [ ] Unit test: system message content after the hook contains the hardening text; all other messages pass through unchanged.

## S2-02 — Implement 5 static attack families (3h) 🔴

- [ ] `naive`: raw injected instruction appended to the tool result with no framing.
- [ ] `ignore-previous`: "ignore previous instructions" framing wrapping the payload.
- [ ] `important-instructions`: decide reuse vs. reimplementation — AgentDojo ships this exact attack and S1-08's reproduction already drove it directly through their pipeline, so wrapping their loader may be cheaper and more faithful than reimplementing.
- [ ] `context-completion`: fake "prior step already completed" framing that primes the model to continue straight into the injected instruction.
- [ ] `encoding-obfuscation`: injected instruction disguised via encoding (e.g. base64/unicode tricks) plus a decode instruction.
- [ ] `important-instructions`, `naive` (→ AgentDojo's `direct`), and `ignore-previous` are already usable as-is via S2-01's `resolve_attack_name` — no new code, just reference them by name in sweep configs. Only `context-completion` and `encoding-obfuscation` are net-new: implement each as a `BaseAttack`/`FixedJailbreakAttack` subclass (per `agentdojo.attacks.base_attacks`) under `attacks/families/`, `register_attack`-ed into AgentDojo's own `ATTACKS` dict, then add both to S2-01's `_FAMILY_TO_AGENTDOJO_NAME` mapping.
- [ ] Unit test per family: given a fixed base tool-result string and payload, the rendered output matches the expected structure.
- [ ] Manual check: run one family live through the adapter on the S1-08 combo with `no_defense`; confirm `security=False` (attack lands) as a sanity check before trusting the full sweep.

## S2-05 — D4 Guard-model classifier (3h) 🔴

- [ ] Design the guard call: a classification prompt sent via `ModelClient` (S1-04) that screens a tool result and returns a numeric score (0–1), not just a binary label — the Sprint 2 acceptance criterion requires a full score distribution for the later ROC curve (S6-07).
- [ ] Implement `defenses/guard_model.py`: `on_tool_result` calls the guard model, compares the score to a threshold, returns `BLOCK`/`ALLOW`, and logs `score` + `threshold` in the `detail_json` passed to `insert_defense_event`.
- [ ] Per Appendix A.4 ("guard model runs on L1 locally"), wire the guard to a cheap local model tier by default.
- [ ] `cost()` sums the guard model's own `CostRecord` across calls in the episode; confirm the adapter also writes those guard-model calls to `cost_record` (via `insert_cost_record`) so guard overhead is separable from base agent cost.
- [ ] Unit test with a mocked `ModelClient`: high-score tool result blocked, low-score allowed, `detail_json` carries the numeric score.
- [ ] Unit test: `cost()` reflects the mocked guard call's cost.

## S2-06 — D5 Canary / known-answer detection (2.5h) 🟡

- [ ] Design the canary probe: an instruction with a known expected literal answer, embedded via `on_pre_generate`.
- [ ] Implement `defenses/canary.py`: track the expected answer across steps (likely via `DefenseContext.metadata`, since state must survive from the probe insertion to checking the model's later response) and flag divergence.
- [ ] Decide the block-vs-log boundary: does a divergence `BLOCK` the next tool call, or only emit a `defense_event` for post-hoc analysis? State the decision in the class docstring, consistent with the S1-02 hook-contract precedent.
- [ ] `cost()` accounts for the probe's token overhead if material, else zero.
- [ ] Register in `defenses/registry.py`.
- [ ] Unit test: divergence from the expected canary answer is detected and logged; a match produces no block.

## S2-07 — D6 Tool allowlist + argument policy (3h) 🔴

- [ ] Design the per-task policy format: allowed tools plus argument constraints (e.g. "recipient must appear in the user's original request") — a small spec per suite/task.
- [ ] Implement `defenses/tool_allowlist.py`: `on_pre_tool_call` checks `tool_call.name` against the allowlist and validates `tool_call.arguments` against the constraint rules, returning `BLOCK` with a reason on violation.
- [ ] Confirm the original user-task text is reachable for argument checks — likely requires extending `DefenseContext.metadata` (`types.py`) to carry it; note if the adapter needs a change to populate it.
- [ ] `cost()` returns zero.
- [ ] Register in `defenses/registry.py`.
- [ ] Unit test: an allowed tool/argument combo passes; a disallowed tool is blocked; an argument failing the constraint (e.g. recipient not in the original request) is blocked with a clear `reason`.

## S2-08 — Security scorer (2h) 🔴

- [ ] Confirm the baseline already exists: AgentDojo's own scoring already flows into `episode.security` via `run_task_with_pipeline`'s return value (per S1-07) — this task is really about adding `partial_compromise`, not building security scoring from scratch.
- [ ] Decide what "partial compromise" means operationally (e.g. the injection task's target tool was called with attacker-influenced arguments, but the full injection task didn't complete) and how to detect it from a reconstructed trace.
- [ ] Add a `partial_compromise` column to `episode` in `trace/schema.sql`, plus the corresponding parameter on `insert_episode`/an update helper in `trace/db.py`.
- [ ] Implement `scoring/security.py`: operates on a completed episode via `trace/queries.py::reconstruct_episode` and writes the label back — a post-hoc pass, not inline in the adapter, so it can be re-run/backfilled.
- [ ] Unit test: a fixture trace with a known injection-task tool call but incomplete follow-through scores `partial_compromise=True, security=False`.

## S2-09 — Utility scorer (2h) 🔴

- [ ] Confirm `episode.utility` (from AgentDojo, already wired in S1-07) already covers benign task completion — this task is mainly about guaranteeing defense-on vs. defense-off comparisons run on the *same* task set, on *injection-free* episodes only.
- [ ] Write the critical assertion called out in the sprint's "Watch for" note and the risk register: utility must only ever be computed on episodes where `injection_task_id IS NULL`. Make it fail loudly, not silently, if violated.
- [ ] Implement `scoring/utility.py`: a query helper computing benign task completion rate per (defense, model) pair, filtered to `injection_task_id IS NULL`.
- [ ] Unit test: feeding the utility scorer an attacked-episode fixture actively fails the assertion — proves the guard exists, not just that it's documented.

## S2-10 — Sweep runner (2.5h) 🔴

- [ ] Add the task-selection dimension that S1-06 explicitly deferred: extend `config/schema.py`'s `ExperimentConfig`/`RunSpec` with a `tasks` field (per-suite list of task IDs) so `expand_run_specs` covers the full grid.
- [ ] Design resumability: before running a (model, defense, suite, attack, task) point, check the trace DB for an already-completed matching episode and skip it.
- [ ] Implement `sweep/runner.py`: expands run specs × tasks, drives `adapters/agentdojo_adapter.py::run_episode` per point.
- [ ] Add bounded parallelism (thread/process pool with a concurrency setting) that respects per-provider rate limits (Appendix A.3's Groq/Gemini free-tier caps) — a simple per-provider semaphore is enough; the full quota-aware run queue is a separate, not-yet-built Appendix A item (S1-11).
- [ ] Add a progress bar (completed/total, elapsed/ETA).
- [ ] Wire `python -m injection_pareto run <config.yaml>` (currently raising `NotImplementedError` per S1-06) to call the sweep runner instead of stopping short.
- [ ] Unit test: a tiny config with some episodes pre-inserted into the trace DB only executes the missing points on resume.

## S2-11 — Run static sweep: 6 defenses × 5 attacks × 3 models × AgentDojo suites (3h wall) 🔴

- [ ] Write `configs/static_sweep.yaml`: all 6 defenses (`no_defense` + D2–D6), all 5 S2-02 attack families, 3 models from `configs/models.yaml` (per Appendix A's ladder), the AgentDojo suites in scope — plus the benign/no-attack points S2-09's utility comparison needs.
- [ ] Kick off overnight per Appendix A.4 guidance; watch for provider rate-limit failures if any hosted model is included.
- [ ] Confirm every defense's `cost()` is separable from base agent cost in the resulting DB (Sprint 2 acceptance criterion) — spot-check a few `cost_record` rows after the run.
- [ ] Confirm the sweep (or a companion run) covers enough injection-free benign episodes per defense to make the utility comparison meaningful.

## S2-12 — `results/static_baseline.md` (1.5h) 🟡

- [ ] Write `scripts/generate_static_baseline.py` (mirrors `scripts/reproduce_s1_08.py`'s pattern): queries the trace DB for security rate, partial-compromise rate, utility rate, and `cost_summary_by_episode` (from `trace/queries.py`), grouped by (defense, attack, model), and renders a markdown table.
- [ ] Include a summary of the guard model's raw score distribution (needed later for S6-07's ROC curve), even if not fully analyzed yet.
- [ ] Confirm the acceptance criterion: the table regenerates from traces with one command — no hand-copied numbers anywhere in the file.
- [ ] Run the script against the S2-11 trace DB and commit `results/static_baseline.md`.

---

## Acceptance criteria (from sprint_planning.md)

- [ ] Every defense reports its own cost via `cost()`; overhead is separable from base agent cost in the DB
- [ ] Security and utility both measured for all 6 defenses on the same task set
- [ ] Guard model emits a full score distribution, not just a binary — needed for the ROC later
- [ ] Results table regenerates from traces with one command (never hand-copy a number)

**Watch for:** the utility scorer must run on **injection-free** episodes only. If utility is measured on attacked runs, every number in the project is wrong — this is exactly what S2-09's assertion test exists to catch.
