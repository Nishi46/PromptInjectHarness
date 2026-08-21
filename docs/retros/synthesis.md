# Retro synthesis (S7-06)

**This is not a set of weekly retros.** `documentation/sprint_planning.md`'s
own ritual ("Friday: 20-minute written retro in `docs/retros/`... those
retro notes become your blog post outline") was never actually followed —
this directory didn't exist before this file. Rather than fabricate 7
sprints of retrospectives with invented specificity, this is a single,
honest synthesis pass over what genuinely does exist: the real
decisions, bugs, and findings already documented (contemporaneously, as
each sprint happened) across `docs/notes/*.md`. Organized chronologically
by sprint; each entry links back to its real source note. This is the raw
material S7-06's blog post draws from.

## Sprint 2 — Defenses + Static Baseline

- Built 6 defenses (spotlighting, instructional prevention, guard-model
  classifier, canary, tool allowlist) behind one 4-hook `Defense` protocol.
- **Real finding, not manufactured**: every defense shows `ASR=0.000`
  against both local models on the static baseline — the local models
  simply never got compromised by any tested attack. The one exception in
  the whole static baseline is `no_defense`/`important_instructions`
  against a hosted 120B model (Groq), ASR=0.333. First appearance of a
  pattern that recurs through the rest of the project.
- (`docs/notes/agentdojo.md` — the terminology/API groundwork this and
  every later AgentDojo-suite sprint built on.)

## Sprint 3 — MCP Tool-Poisoning Suite

- Built a second, AgentDojo-independent suite modeling an attack surface
  AgentDojo doesn't cover at all: injection through a tool's *description*
  (schema), not its *output* (data) — 15 mock servers, 15 benign tasks, 41
  poisoned-description cases across 4 mutation sub-families.
- **Real bug-hunting story**: the first full 228-episode dry run failed the
  sprint's own ≥70% undefended-utility acceptance gate badly (26.7%/33.3%).
  Root-causing against real traces (not assumptions) found and fixed 5
  distinct real issues in one sitting: a fallback tool-call parser for
  weak models that describe an action instead of calling it,
  `temperature=0` to kill non-deterministic run-to-run noise, a scorer bug
  requiring exact matches on free-form generated text, several genuinely
  ambiguous task prompts, and an argument matcher too strict about
  JSON-string-encoded numbers/lists. Utility went from failing badly to
  73–80%, comfortably clearing the gate.
- **Real, kept finding, not re-engineered away**: even after every fixable
  bug was fixed, `ASR=0.000` for every defense on both local models,
  undefended included — the same capability-ceiling pattern Sprint 2
  found, now confirmed on a structurally different suite too.
- (`docs/notes/mcp_suite.md`)

## Sprint 4 — Adaptive Attack Engine

- Built an LLM-driven mutation loop: up to 20 refinement rounds per
  (defense, attack, model) trial, an attacker model rewriting its own
  payload against feedback from each round's result.
- **Real bug #1**: the very first live smoke test showed the mutator
  silently never mutating anything across all 20 rounds. Root cause: the
  family-drift heuristic flagged a perfectly good rewrite as "drifted"
  because the *task's own benign content* (not the model's rewrite)
  happened to contain another family's marker phrase. Fixed by stripping
  the known-legitimate goal text out of the payload before scanning for
  drift markers.
- **Real bug #2, more consequential**: the first full 60-trial sweep
  crashed 27 of 60 trials (45%) with a YAML parse error. Root cause: found
  inside *AgentDojo's own* environment-injection code, which substitutes
  attack payloads into raw YAML text via `str.format` before parsing it —
  a free-form LLM-mutated payload containing an unescaped quote breaks the
  surrounding YAML and the parse fails before the agent even runs. A real
  structural fragility in a widely-used benchmark framework, not this
  project's own bug, only ever exposed because an LLM inventing free text
  (unlike this project's own prior hand-authored templates) has no
  guarantee against it. Fixed with per-round failure isolation: a round
  that fails to execute is scored as "no compromise" and the trial
  continues, rather than the whole trial aborting.
- **Real, honest headline result**: `ASR@1 == ASR@20 == 0.000` across every
  defense × attack family × local model, both suites — 20 rounds of
  real LLM-driven mutation found nothing that static testing hadn't
  already blocked, locally. Extends Sprints 2–3's static null result to
  the adaptive setting rather than overturning it.
- (`docs/notes/adaptive_attacks.md`)

## Sprint 5 — Architectural Defenses

- Built two structurally different defenses: `dual_llm` (D7, a quarantined
  reader LLM that reads untrusted tool output and returns only a
  structured summary) and `capability_enforcement` (D8, a lightweight
  CaMeL-style data-provenance policy — tags values seen in tool results,
  blocks a "sink" tool call whose argument carries a tainted value not
  also present in the user's own original request).
- **Real, bidirectional utility finding, not a one-sided tax**: `dual_llm`
  helped one real (model, task) pair recover from a badly-formatted error
  message, and hurt a different one by rephrasing a "not found" error in a
  way that discouraged a retry the raw error text would have prompted —
  inspected via the actual traces in both directions, not assumed from
  the architecture alone.
- **The centerpiece finding**: one real adaptive trial (out of 18) landed
  a genuine compromise against `dual_llm` — the *only* non-null adaptive
  security result this project's entire local-model grid ever produced.
  Inspecting exactly how it landed showed something structural, not
  incidental: the mutation's real effect wasn't making the injected text
  more persuasive, it was making the model choose to call the poisoned
  tool *at all*. Once called, the compromise rode in on that tool's
  **description** — and `dual_llm`'s quarantine only ever inspects a
  tool's **result**, a completely different channel. `dual_llm` is blind
  to schema-path injection by construction, proven with a real landed
  compromise rather than left as an untested hypothesis. `capability_enforcement`
  was never compromised on the same case, for an equally structural reason
  (its check runs on every call regardless of why the model made it).
- **A second, real demonstration, built to distinguish D8 from the
  simpler D6 (tool-allowlist) defense**: authored one new poisoned case
  routing a sensitive value through a message *body* (an argument no
  name-based allowlist inspects) to an otherwise fully legitimate
  recipient. Identical model, identical task, identical attempted call:
  `no_defense` and `tool_allowlist` both let it through; `capability_enforcement`
  alone blocked it — and it blocked by tracing *where the value came
  from*, not by pattern-matching the call's content or destination.
  Getting a real model to actually attempt the compromise took two design
  iterations (a first, two-hop version reliably failed to get sequenced
  correctly by any real model tested); moving the sensitive field onto an
  already-fetched result fixed that without weakening what the case
  proves.
- (`docs/notes/architectural_defenses.md`)

## Sprint 6 — Composition + Model Sweep

- Composed multiple defenses into ordered stacks with real per-layer trace
  attribution (which specific member blocked, or spent money, inside a
  composed pair) — not just an outer label.
- **Real finding**: composing `spotlighting` *ahead of* `guard_model`
  measurably and consistently degrades the guard's own classification
  input — confirmed across 7 real matched pairs, all 7 in the same
  direction. Doesn't move ASR (the guard's block is a no-op today), but a
  real, load-bearing interaction effect a composition matrix built purely
  from solo-defense numbers would have missed entirely.
- Expanded the model ladder to 6 tiers across 4 providers, building two new
  API clients from scratch. **Two real integration bugs, both found only
  under the real adapter's full tool-schema shape, not a minimal smoke
  test**: Gemini's restricted parameter-schema subset rejects
  `$defs`/`$ref`/`additionalProperties` that Pydantic's JSON-schema output
  routinely emits; and a required `thoughtSignature` field has to be
  echoed back verbatim when a function call is replayed into a later
  turn's history, undocumented clearly enough to have been anticipated.
  **A real infrastructure finding too**: Gemini's actual free-tier cap
  turned out to be 20 requests/day for the specific model used — almost
  two orders of magnitude tighter than the general estimate planned
  around, discovered by hitting it live.
- Built a guard-model ROC curve. **Two AUC numbers that disagreed
  sharply**: a fixed-threshold-grid trapezoidal AUC (0.423, worse than
  random) versus a threshold-free Mann-Whitney AUC (0.875, genuinely
  strong). Root cause: the guard model's real scores cluster at round
  numbers, and the grid's own `0.50` sample point collided with a mass of
  tied scores under the defense's *strict* `>` threshold rule, clipping an
  entire tied cluster at exactly the wrong point. The grid number was a
  measurement artifact of the method, not a property of the classifier —
  caught by cross-checking two independent computations against each
  other rather than trusting the first one.
- (`docs/notes/composition.md`, `docs/notes/model_sweep.md`,
  `docs/notes/guard_model_roc.md`)

## Sprint 7 — Analysis & Release

- **A real scoping problem, not a data problem**: the sprint plan's own
  Pareto-plot spec asked for "adaptive ASR" on the X axis — but adaptive
  ASR is `0.000` in all 108 real (suite, defense, attack, model) cells
  this project has ever measured. Resolved with two honestly-labeled
  panels rather than silently substituting one population for the other:
  the real adaptive null result, plus a second panel from the one place
  real ASR variation exists at all (a single paid hosted model).
- **A number caught wrong before it shipped**: an earlier "116 real
  adaptive trials" claim, repeated across three files, turned out to have
  never actually been verified — recounted directly from the trace DBs
  mid-task: 108 cells, 360 underlying trials. Fixed everywhere it had been
  repeated, not just where it was first noticed.
- **A real, previously-undiscovered environment bug found while verifying
  `make reproduce`**: this machine's default `python3` resolves to 3.9.6,
  two minor versions below what the project actually requires — `pip
  install -e` fails with a confusing, not-obviously-version-related error
  on the wrong interpreter. Found only by actually testing the documented
  setup steps in a genuinely clean `git worktree`, not by reading the
  config. Two independent live full-sweep runs (6:50 and 10:02) and one
  idempotency check (a second run: 2.7 seconds) confirmed the real,
  measured reproduction story the README claims, rather than an assumed
  one.
- (`docs/notes/release.md`)

## What this synthesis is *for*

S7-06's blog post draft leads with the three claims (reframed honestly
against what the real data actually supports, not the sprint plan's
original assumptions) and draws its "found a bug, fixed it" narrative
material directly from the entries above — real, sourced, and
contemporaneously documented, even if never assembled into a weekly retro
at the time.
