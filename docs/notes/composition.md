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
