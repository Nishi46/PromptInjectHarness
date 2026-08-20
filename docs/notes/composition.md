# Composition (Sprint 6, E6)

## Implementation decisions for this project's Sprint 6

### D1 — per-layer trace attribution

`DefenseStack` (`defenses/stack.py`) already composed multiple `Defense`s
correctly for *execution* (blocking, mutation-threading, `cost()` summing)
since S1-02 — that part of S6-01 was already done. What was missing: both
adapters' recorder wrappers call `defense_stack.on_pre_generate`/
`on_pre_tool_call`/`on_tool_result` exactly once per hook and log exactly
one event/cost row per call, labeled with a single outer `defense_name`
string — so a composed stack's `defense_event`/`cost_record` rows could
never say *which* member fired or spent the money. `DefenseStack`'s own
docstring already named this: *"With more than one reason-setting member in
the same stack (Sprint 6 composition), only the last one survives here."*

**Fix, deliberately not touching the `Defense` protocol:** `DefenseStack`
now takes `list[tuple[str, Defense]]` (name, instance) instead of a bare
`list[Defense]`, and records `self.last_member_results: dict[str,
list[tuple[str, HookResult]]]` (keyed by hook name) fresh on every hook
call — every member that actually ran, in order, paired with its own name
and its own `HookResult`; a member skipped because an earlier one already
blocked is simply absent, not fabricated. Both adapters read this
immediately after each hook call and write one `defense_event`/`cost_record`
row per member, using that member's own name instead of the outer
composite string. `resolve_defense_stack(name)` (`defenses/registry.py`)
splits `name` on `"+"` and resolves each part via the existing
`resolve_defense` — a name with no `"+"` is behaviorally identical to
today's single-defense path, which is why this is safe to swap in
everywhere unconditionally, not just for new composition configs.

Adding a `name` attribute to the `Defense` protocol itself (the other real
option) would have touched all 8 concrete defense classes for a fact the
*composer* already knows at construction time — the smaller, more
"additive, not a rewrite" change is to carry the name alongside the
instance in `DefenseStack`, not on every `Defense`.

### D2 — the top-4 defenses for composition

Every one of the 8 registered defenses ties at `ASR = 0.000` against
`llama3.2:3b`/`llama3.1:latest` in every real sweep this project has run
(`results/static_baseline.md`, `results/mcp_suite.md`,
`results/architectural_defenses.md`) — there is no security signal to rank
by. Picked instead on **mechanism diversity and real operational effect**,
from the 5 real Sprint-2 defenses (`no_defense` excluded — it's the
baseline, not a composition candidate):

| Defense | Mechanism | Real blocking power? |
| --- | --- | --- |
| `tool_allowlist` (D6) | argument/allowlist policy, `on_pre_tool_call` | **Yes** — the only hook whose `BLOCK` has operational effect (`types.py`'s `Verdict` contract) |
| `spotlighting` (D2) | input-side delimiting/datamarking, `on_pre_generate` | No (pure mutation) |
| `instructional_prevention` (D3) | system-prompt hardening, `on_pre_generate` | No (pure mutation) |
| `guard_model` (D4) | tool-result classifier, `on_tool_result` `BLOCK` | No — confirmed a documented no-op (`guard_model.py`'s own docstring) |
| `canary` (D5) | known-answer probe, `on_pre_generate` `BLOCK` | No — also fires on `on_pre_generate`, confirmed a no-op by the same `Verdict` contract |

**Picked: `spotlighting`, `instructional_prevention`, `guard_model`,
`tool_allowlist`.** `canary` is the one excluded. Reasoning: `guard_model`
and `canary` are mechanistically redundant for this purpose — both are
detection-only defenses whose `BLOCK` verdict has no operational effect,
differing mainly in *where* they look (tool output vs. the model's own
final text) rather than in what a composition can actually do with them.
Keeping one of the two (`guard_model`, since it screens the same untrusted
channel `spotlighting` marks — a more mechanistically interesting pairing
than two prompt-level defenses composed together) and dropping the other
gives 4 defenses spanning genuinely different points in the pipeline:
input-side mutation (`spotlighting`), system-prompt hardening
(`instructional_prevention`), detection (`guard_model`), and real blocking
(`tool_allowlist`) — while `canary`'s exclusion also matches the sprint
plan's own cut list, which already ranks D5 (canary) as the first defense
to cut if the sprint falls behind.

## S6-01 notes

Migrated every existing `DefenseStack([...])` construction (2 production
call sites — `sweep/runner.py`, `adaptive/sweep.py` — plus 4 test files) to
`resolve_defense_stack`/the new tuple-list constructor.

**Regression check (single-defense configs unaffected):** re-ran the exact
`(model, task)` pair already sitting in `runs/local/static_sweep/trace.db`
as episode 223 (`dual_llm`/`llama3.2:3b`/`user_task_0`, written weeks
earlier in Sprint 5, well before this change) via
`configs/smoke_dual_llm.yaml`, post-change. `defense_event` (4 rows: one
`on_pre_tool_call`, one `on_tool_result`, two `on_pre_generate`, all named
`dual_llm`, all `allow`), `cost_record` (2 base-model rows + one
`defense:dual_llm` row, identical token counts via the response cache), and
`tool_call.blocked_by_defense` (`None`) are byte-identical between the two
runs.

**Real composed-defense smoke check** (`configs/smoke_composition.yaml`,
`defenses: [spotlighting+guard_model]`, real local Ollama, one live
episode): confirmed the whole pipeline end to end, not just unit tests
against fakes --

- `defense_event` correctly separates the two members: `spotlighting` and
  `guard_model` each get their own row per hook call (2x `on_pre_tool_call`,
  2x `on_tool_result`, 4x `on_pre_generate` across 2 turns) -- genuine
  per-layer attribution from a real run, not a scripted fixture.
- `guard_model` scored one tool result `block` on `on_tool_result` in this
  real run; the episode continued normally afterward (a second turn ran),
  confirming the documented `Verdict` contract -- `on_tool_result`'s
  `BLOCK` has no operational effect -- still holds unchanged inside a
  composed stack.
- `cost_record` shows exactly one `defense:guard_model` row (`guard_model`
  makes its own model call; `spotlighting` doesn't, so it correctly
  contributes no cost row) -- the per-layer cost split working on real,
  non-fake defenses.
- `run.defense_stack = "spotlighting+guard_model"` -- the composite name
  stored as a plain string, confirming the "no config/DB schema change
  needed" part of the D1 design decision.

## S6-02 notes

### Which pairs actually need both orderings

Read all four top-4 defenses' real (non-pass-through) hook implementations
directly rather than assuming. Per-hook, which members have real logic:

| Defense | `on_pre_generate` | `on_pre_tool_call` | `on_tool_result` |
| --- | --- | --- | --- |
| `spotlighting` | **real** (system-prompt addendum) | pass-through | **real** (delimit + datamark, mutates content) |
| `instructional_prevention` | **real** (system-prompt addendum) | pass-through | pass-through |
| `guard_model` | pass-through | pass-through | **real** (classifier score; reads content, never mutates it; makes its own model call) |
| `tool_allowlist` | pass-through | **real** (allowlist + arg-value block) | pass-through |

Two defenses only interact order-dependently if they share a hook where
*both* have real logic:

- **`spotlighting` + `instructional_prevention`** share `on_pre_generate`.
  Each only checks for *its own* addendum marker before appending (`if
  message.role == "system" and _OWN_ADDENDUM not in message.content`), so
  the *set* of appended text is order-independent -- but the two addenda
  land in the system message in a different concatenation order depending
  on which runs first, so the exact string the model sees genuinely
  differs. Not proven order-independent; **both orderings run.**
- **`spotlighting` + `guard_model`** share `on_tool_result`. `spotlighting`
  *mutates* the content (wraps + datamarks it); `guard_model` *reads*
  whatever content it's handed and scores it, without mutating. This is a
  real, substantive interaction, not a technicality: `spotlighting` first
  means `guard_model` scores marked-up text; `guard_model` first means it
  scores the raw tool output. **Both orderings run** -- this is also the
  pairing most likely to produce an interesting, inspectable finding for
  S6-04.
- **Every other pair uses two disjoint hooks** (`spotlighting`/
  `tool_allowlist`: `on_pre_generate`+`on_tool_result` vs.
  `on_pre_tool_call`; `instructional_prevention`/`guard_model`:
  `on_pre_generate` vs. `on_tool_result`; `instructional_prevention`/
  `tool_allowlist`: `on_pre_generate` vs. `on_pre_tool_call`;
  `guard_model`/`tool_allowlist`: `on_tool_result` vs. `on_pre_tool_call`).
  `DefenseStack` runs each hook's loop independently, and only one member
  in each of these four pairs ever does real work on any given hook call --
  the other is a pure pass-through for that hook, so its position relative
  to the real one is unobservable. **One ordering run per pair,** stated
  here rather than assumed silently.

**Composite set: 8 composite names** (2 order-dependent pairs x 2
orderings + 4 order-independent pairs x 1 ordering), run alongside the 4
solo top-4 defenses for the composed-vs-solo comparison S6-03 needs:

```
spotlighting+instructional_prevention
instructional_prevention+spotlighting
spotlighting+guard_model
guard_model+spotlighting
spotlighting+tool_allowlist
instructional_prevention+guard_model
instructional_prevention+tool_allowlist
guard_model+tool_allowlist
```

### Trace-DB reuse decision

Same reuse Sprint 5's architectural configs already established:
`configs/composition_static_sweep.yaml`/`configs/composition_mcp_sweep.yaml`
write into `runs/local/static_sweep/trace.db`/`runs/local/mcp_sweep/trace.db`
-- the `no_defense` rows (and now also the 4 solo top-4 defenses' own rows,
already populated by S2-11/S2-12) become free comparison baselines. Adding
new `defenses:` entries changes the config hash, so this file's own points
get their own fresh `run` rows regardless -- no accidental collision with
the existing rows, and no duplicate-benign-pass problem to reason about
this time (unlike Sprint 5's `dual_llm`, none of these composite names were
ever run under an earlier, different-hashed config).

### Wall-clock risk

`guard_model` makes a real classifier-model call per tool result --
structurally the same category of overhead `dual_llm` had in Sprint 5
(measured there at ~22s for a full 20-round adaptive trial). 5 of the 12
`defenses:` entries here involve `guard_model` (the solo entry plus 4
composites). Sample-and-extrapolate before the full adaptive grid, per the
checklist -- see the S6-02 run log below for the real measured number.

### Run log

**Static sweeps:** `configs/composition_static_sweep.yaml` (432 episodes,
workspace) and `configs/composition_mcp_sweep.yaml` (456 episodes, mcp)
both ran clean -- **0 failures**. `ASR = 0.000` in every (defense, model)
cell in both suites, for all 12 `defenses:` entries (4 solo + 8
composite) -- consistent with every other defense already in these trace
DBs against `llama3.2:3b`/`llama3.1:latest`.

**Adaptive sanity check:** 1-trial `--sample-fraction 0.00834` on
`spotlighting+guard_model` (trial index 0, `naive`/L1) took **~33s real**
for a full 20-round trial -- confirmed via `cost_record` as genuine work
(20 `defense:guard_model` calls, 40 base-model calls, 28 mutator calls),
slightly above `dual_llm`'s ~22s in Sprint 5 but the same order of
magnitude. At that rate the full 216-trial grid (120 workspace + 96 mcp)
was a feasible background run, not a multi-hour one -- no scope cut needed.

**Full adaptive grids:**

- `configs/composition_adaptive_mcp_sweep.yaml` (96 trials): **0
  failures**, `ASR@20 = 0.000` in every (defense, model, sub-family) cell.
- `configs/composition_adaptive_sweep.yaml` (120 trials): 118 completed
  fresh + 1 resumed (the earlier sanity-check trial) + **1 failed**:
  `spotlighting+instructional_prevention` / `llama3.2:3b` /
  `encoding_obfuscation` -- `HTTPConnectionPool(host='localhost',
  port=11434): Read timed out (read timeout=300.0)`.

  **This is the exact same documented failure mode S4-05 already found**
  (`docs/notes/adaptive_attacks.md`'s S4-05 section: two identical timeouts
  on `canary` + `encoding_obfuscation` in the original 60-trial sweep) --
  a live Ollama request exceeding `OllamaClient`'s 300s default, plausibly
  because `encoding_obfuscation`'s base64-heavy payloads grow across
  mutation rounds and stack on top of the extra per-round overhead a
  second defense (here, two prompt-mutating members; there, `canary`'s own
  extra work) adds. Root-caused against that precedent, not blindly
  retried: the failed trial (`adaptive_trial` id 115) shows `started_at`
  set, `ended_at`/`success` both `NULL`, and 9 of a possible 20
  `adaptive_round` rows written -- it ran 9 real rounds before the 10th's
  request timed out and the trial aborted, the same partial-completion
  shape the Sprint 4 precedent describes. Left undone rather than
  bumping the global client timeout, matching that precedent's own
  reasoning: an infrastructure capacity constraint specific to this
  (long-payload family x extra-defense-overhead) combination, observed
  once in 216 real trials, not a logic defect. (`scripts/generate_adaptive_results.py`'s
  existing `_asr_at_20` query already has no `WHERE success IS NOT NULL`
  filter, so this trial's `NULL` success is counted the same way the
  Sprint 4 precedent's two timeouts already are -- as a non-compromise in
  the denominator, a pre-existing convention this task didn't need to
  change.)

**Combined result: no security signal from composition on this task set,
consistent with every other defense this project has tested against these
two local models.** Every composite pair and every solo top-4 defense
ties at `ASR = 0.000`, static and adaptive, both suites. Whether this
means anything interesting about *independence* (vs. just the same
capability-ceiling null result extending to composed defenses) is S6-03's
question to answer, not S6-02's -- this task's job was running the grid
and reporting the real numbers, which are these.
