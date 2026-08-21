# Release notes (Sprint 7)

## S7-01: the Pareto plot

### E1's two-panel decision, confirmed and refined against the real data

`documentation/sprint7_checklist.md`'s E1 already established that adaptive
ASR@20 is `0.000` in all 116 real rows (`results/adaptive.md`) -- no
dynamic range to plot on an "adaptive ASR" axis. Two more real gaps turned
up while actually pulling the data, both resolved here rather than
discovered mid-script:

**Adaptive trials have no utility measurement at all.** `results/adaptive.md`
has no utility section -- an adaptive trial is a pure attacked-episode
budget search (S4-03's design), never a benign pass. So "utility retention"
for the adaptive panel can't come from adaptive data either, by
construction, not just because it happens to be flat.

**The L5 (Groq) composition slice has benign utility for exactly one
defense.** Checked directly (`SELECT ... WHERE r.model='openai/gpt-oss-120b'
GROUP BY defense_stack, attack`): only `no_defense` has any
`injection_task_id IS NULL` episodes on L5 -- `configs/composition_groq_slice.yaml`'s
own header comment says why ("this slice is a pure security-signal
experiment, not a utility measurement, so it doesn't need one"). The other
6 defenses in that slice have zero benign L5 episodes.

**Resolution, both panels:** the utility axis for *both* panels is this
project's own most complete utility dataset -- the mean benign utility rate
across the two local models (`llama3.2:3b`, `llama3.1:latest`), per
defense, from `runs/local/static_sweep/trace.db` (`scoring.utility.benign_utility_rate`).
This is a deliberate, explicitly-labeled cross-population combination:
security (ASR) for panel 2 is measured on L5 because that's the only place
it varies; utility is measured on the local models because that's where
*it's* actually measured for every defense in the plot. Stated plainly in
`results/pareto.md` and in the plot's own caption, not left implicit.

### Scope: workspace suite only

Both panels are scoped to the workspace (AgentDojo) suite, not pooled with
MCP. This project's `results/adaptive.md`/`results/composition.md` both
already break results out by suite rather than pooling them, and pooling
two suites with structurally different injection mechanisms (S6-07's own
finding: MCP's injection lives in a tool *description*, workspace's in a
tool *result*) into one ASR number would blend two different things under
one label -- the same reasoning composition/S6-07 already applied,
extended here.

### Panel definitions (real data, exact source)

**Panel 1 — adaptive (the honest null result).** 6 defenses
(`no_defense`, `spotlighting`, `instructional_prevention`, `guard_model`,
`tool_allowlist`, `canary`) -- `results/adaptive.md`'s own workspace-suite
grid. X = ASR@20 (`generate_adaptive_results.py::_asr_at_20` +
`_defense_level_asr`, reused via the same `importlib`-from-file-path
technique the test suite already uses for cross-script reuse without a
package). Y = mean local-model benign utility. Bubble = mean $/episode on
the same local models -- **exactly `$0.000000` for every point**, not
missing data: Ollama inference is free by construction (confirmed:
`SELECT SUM(usd) FROM cost_record` over the whole adaptive trace DB is
`0.0` across 11,856 rows). The plot says this explicitly rather than
showing uniformly-sized bubbles with no explanation.

**Panel 2 — static, L5 (the one place ASR varies).** 6 defenses
(`no_defense`, `spotlighting`, `instructional_prevention`, `tool_allowlist`,
`spotlighting+instructional_prevention`, `instructional_prevention+tool_allowlist`)
-- exactly `configs/composition_groq_slice.yaml`'s set plus `no_defense`
(S2-11's own Groq slice); `guard_model` and its composites are absent
because that slice deliberately excluded them (its own header comment:
`guard_model`'s block is a documented no-op, so a `guard_model`-composed
pair is architecturally guaranteed to match its other member's solo ASR --
not worth spending Groq quota to confirm). X = static ASR on
`openai/gpt-oss-120b`, `important_instructions`, `n=3`/cell. Y = same
local-model utility measure as panel 1. Bubble = mean $/episode on L5
itself (real, non-zero, genuinely varies: `$0.000054` to `$0.000272`
across the 6 defenses -- Groq is a real priced API, unlike Ollama).

### Frontier computation

`pareto_frontier()`: a point is non-dominated iff no other point has
ASR ≤ its ASR *and* utility ≥ its utility, strictly better on at least one
axis. Computed directly, not eyeballed off the rendered chart.

### E2: matplotlib added as a deliberate exception to the no-new-dependency precedent

Every prior stats computation in this project (`spearman_rho`, `_percentile`,
`mann_whitney_auc`) deliberately hand-rolled math specifically to avoid a
`scipy` dependency for what was always a single number. A rendered,
labeled, two-panel bubble chart with a highlighted frontier is a
qualitatively different task -- hand-rolled SVG generation for that is a
lot of low-value layout code. Added `matplotlib>=3.9` to `pyproject.toml`
(same `>=`-pinning style already used for `requests`/`pyyaml`/`tqdm`), a
real new dependency, on purpose, documented here rather than silently
introduced.

## S7-02: defense selection guide -- the judgment layer

`results/selection_guide.md` (`scripts/generate_selection_guide.py`) has the
numbers; this section has the actual recommendation each one supports --
judgment, not something a query can produce, per S6-06's own "numbers in
`results/`, judgment in `docs/notes/`" precedent.

**`instructional_prevention` is this guide's strongest real recommendation.**
It's the only defense that (a) blocked the one real attack this project has
ever measured on a paid model (L5 ASR 0.000 vs. `no_defense`'s 0.333), and
(b) costs literally nothing (`$0`, `0ms`, pure prompt-text prepend). The
honest caveat: that's `n=3` episodes on one attack family against one
hosted model -- real signal, not proof, and its own utility numbers are
noisy at that sample size (`1.000` on `llama3.2:3b`, `0.333` on
`llama3.1:latest` -- a small-sample split, not a stable per-model
difference this project can confidently explain yet).

**`tool_allowlist` is the second strongest, on the same logic.** Same real
L5 block (ASR 0.000), same `$0`/`0ms` cost, structurally guaranteed rather
than prompt-compliance-dependent (a hard `on_pre_tool_call` block, not a
request to the model to behave). Recommend when the task's real tool
surface is small/known enough to allowlist up front; it can't help against
an attack that stays within already-permitted tools.

**`spotlighting` measured *zero* real security benefit on the one signal
available.** L5 ASR is `0.333` -- identical to `no_defense`'s own `0.333`
-- despite being a real, structurally different prompt-transform defense.
Worth stating plainly rather than assuming a mechanism this different must
have helped: on this project's own data, it didn't, at least not against
`important_instructions`. Its `$0` cost still makes it cheap to stack
alongside something that does show a real effect (see the composition
caveat below) -- just don't recommend it standalone as security-load-
bearing on this evidence.

**`guard_model` is a real detector, not yet a real defense.** Its own ROC
curve (`results/guard_model_roc.md`) shows genuine discriminative power --
Mann-Whitney AUC 0.875, the best real signal-quality number in this whole
project -- but `guard_model.py`'s own docstring is explicit that its
`BLOCK` verdict is a documented no-op today: nothing downstream actually
stops the call. Recommend it today only as a monitoring/scoring signal
(log the score, alert a human), not as an active defense, until that
verdict is wired to something operational. Its real cost is latency, not
money: ~959ms/episode on this project's own local classifier model
(`llama3.2:3b`) -- would become a real `$` cost too on a paid classifier
model, which this project has never measured.

**`canary` is redundant with `guard_model`, not a distinct recommendation.**
S6-01's own defense-selection note (`docs/notes/composition.md`) already
named it "mechanistically redundant with `guard_model`" when picking the
composition top-4 -- restated here for the same reason: no real security
signal (never run on L5), `$0`/`0ms` cost, but nothing this guide's data
gives it that `guard_model` doesn't already cover better (`guard_model`'s
real ROC curve vs. `canary`'s un-measured one).

**`capability_enforcement` (D8) and `dual_llm` (D7): the real, structurally
guaranteed cost asymmetry, restated directly (S7-02's own explicit ask).**
`capability_enforcement` shows `$0` **and** `0ms` overhead -- not a small
number, an exact, structural zero, confirmed identically in both this
guide's own query and `results/architectural_defenses.md`'s independent
one: its whole mechanism (provenance-tag policy check) makes no model
call, by design. `dual_llm` shows real, substantial overhead --
**~5012ms/episode**, over 5x `guard_model`'s own latency tax -- from its
quarantine LLM call, and would show real `$` cost too on a paid quarantine
model (this project's is local/free). These are not "the same kind of tax
at different magnitudes": one architecture is free by construction, the
other pays a real per-call inference cost regardless of outcome. Recommend
`capability_enforcement` whenever the task's data-flow/capability policy is
expressible (it's strictly cheaper for the same category of protection)
and `dual_llm` when the policy can't be expressed that way but the
quarantine latency is affordable.

**The composition caveat (S6-04), restated for anyone stacking defenses.**
`docs/notes/composition.md`'s S6-04 finding: composing `spotlighting`
*ahead of* `guard_model` in a stack measurably and consistently degrades
`guard_model`'s own classification input (7/7 real matched pairs, same
direction) -- `spotlighting`'s prompt rewriting changes what `guard_model`
actually sees. Operationally: if using both, order matters and isn't free
to guess -- check the specific ordering against that note before deploying
either one, and don't assume composing two defenses is strictly additive
either way: S6-03's own independence-formula work found the opposite can
also happen (a composed pair *outperforming* the naive prediction), so
neither direction should be assumed without checking the specific pair.
