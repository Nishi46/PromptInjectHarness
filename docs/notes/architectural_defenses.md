# Architectural defenses (Sprint 5, E5)

## Implementation decisions for this project's Sprint 5

Both D7 and D8 must plug into the *existing* `Defense` protocol
(`on_pre_generate`, `on_pre_tool_call`, `on_tool_result`, `cost`) and the
*existing* `DefenseStack`/`resolve_defense`/`run_sweep`/`run_adaptive_sweep`
machinery unmodified — the same "additive, not a rewrite" discipline
Sprint 4's preamble established for the adaptive loop.

### D7 (dual-LLM)

"Privileged planner with tool access; quarantined reader processes untrusted
content and returns only structured, non-instructional summaries" maps
entirely onto `on_tool_result`.

- The **privileged planner** is simply the episode's existing `model_client`
  (unchanged, still sees the whole transcript, still calls tools) — there is
  no new planner-side machinery.
- The **quarantined reader** is a *second* `ModelClient`, invoked only inside
  the new defense's `on_tool_result`, exactly mirroring
  `GuardModel.on_tool_result`'s "extra model call, log to `self.responses`"
  shape (`defenses/guard_model.py`) — except where `GuardModel`
  scores-and-passes-through, this defense summarizes-and-*replaces*: it
  returns a new `ToolResult` whose `content` is the quarantined model's
  structured summary, never the raw tool output.

Why this avoids touching `Defense`/`DefenseStack`/either adapter:

- Both adapters' `_write_episode_trace`/`_write_mcp_episode_trace` already
  walk `defense_stack.defenses` for any member exposing a
  `responses: list[ModelResponse]` attribute and write those calls to
  `cost_record` labeled `defense:<name>` — so the quarantined model's cost is
  captured with **zero adapter changes**, the same reuse `GuardModel` already
  proved out.
- Content-mutation through `on_tool_result` is already a first-class, tested
  path in both adapters (`post_result.value.content` / `final_result.content`
  in the turn loops) — no protocol or adapter change needed anywhere for D7.

### D8 (capability enforcement) — explicitly the sprint plan's "reduced CaMeL" scope valve

A literal CaMeL implementation (planner emits a restricted program in a
custom DSL; a bespoke interpreter executes it while tracking provenance per
value) would replace the entire tool-calling loop in both `run_episode` and
`run_mcp_episode` — not a `Defense` hook, a different execution model
entirely, and not reusable by `sweep/runner.py`/`adaptive/sweep.py` without
touching both. Out of scope by the sprint's own valve.

Instead:

- **Provenance tagging** happens in `on_tool_result` — a cheap, documented
  regex heuristic (`_looks_in_family`-style: "a heuristic, not a classifier")
  extracts candidate values (emails, URLs, quoted substrings) from every
  tool result's `content` into a per-episode `set[str]` the defense instance
  owns (fresh per episode, since `resolve_defense`/`defense_stack_factory`
  already construct a new instance per episode/round — S4-03's precedent, no
  new lifecycle needed).
- **The policy engine** is a check inside `on_pre_tool_call`, gating only a
  documented, small, per-suite set of "sink" tools (S5-04): if a sink call's
  string argument (or list-of-string element, mirroring `tool_allowlist.py`'s
  own value-scanning loop) matches a tainted value that does *not* also
  appear verbatim in `context.metadata["user_task_prompt"]` (the same
  trusted-origin allowance `ToolAllowlist` already grants), `BLOCK` with a
  reason naming the tainted value and the sink policy — the exact
  operational hook (`on_pre_tool_call`'s `BLOCK`) `ToolAllowlist` already
  uses, so again **zero adapter changes**.

Distinction from D6/`ToolAllowlist`: D6 checks a fixed set of argument
*names* against the user's own trusted prompt; D8 checks *any* string
argument on a *sink tool* against values whose provenance is a tool result
seen during the episode — a value-provenance check, not a name-keyed one, so
it can (and D6 structurally cannot) catch a secret leaking through a
non-destination argument, e.g. a file's real contents echoed into a message
body sent to an otherwise-legitimate-looking recipient.

## S5-01: `DualLLM` (D7) notes

- Implemented in `src/injection_pareto/defenses/dual_llm.py`, registered as
  `"dual_llm"` in `defenses/registry.py`.
- Known trust boundary (documented in the module docstring and exercised by
  a unit test): the quarantine model is still an LLM reading
  attacker-controlled text. If its own output is itself instruction-shaped
  (e.g. it echoes "IGNORE PREVIOUS INSTRUCTIONS" into its summary), `DualLLM`
  does not re-validate that output — it is substituted into the transcript
  verbatim as data. D7 is a mitigation, not a guarantee, exactly like every
  other Sprint 2 defense.
- Integration check (real local Ollama, `configs/smoke_dual_llm.yaml` --
  `configs/smoke.yaml` with `defenses: [dual_llm]` swapped in, single
  `workspace`/`user_task_0` benign episode):
  `python -m injection_pareto run configs/smoke_dual_llm.yaml --concurrency 1`
  produced `runs/local/smoke_dual_llm/trace.db`, episode id 1. Confirmed
  both wiring properties directly from that trace DB:
  - (a) `tool_call.result_json` for the episode's one `get_day_calendar_events`
    call is `"* No relevant information provided."` -- plainly the
    quarantine model's plain-text bullet-point summary format, not the raw
    structured calendar-event data the tool actually returned. The
    privileged planner only ever saw this summary.
  - (b) `cost_record` has one row with `model = 'defense:dual_llm'`
    (`usd=0.0, tokens_in=112, tokens_out=7`) alongside the two base-model
    `llama3.2:3b` rows for that episode -- the quarantine call's cost is
    captured separately with zero adapter changes, via the same
    `responses: list[ModelResponse]` duck-typed path `GuardModel` already
    proved out in S2-05.
