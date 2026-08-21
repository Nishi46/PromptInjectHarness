# Guard-model ROC curve (Sprint 6, S6-07)

## D4: resolving the ground-truth signal, before writing any query

The sprint plan's own preamble (D4) flags that this project has never
needed a per-tool-result ground-truth label before -- every prior
`guard_model` number (`results/static_baseline.md`'s score-distribution
section) is a plain descriptive summary over scores, no labels attached.
A ROC curve needs a binary "was *this specific* tool result the injection
point" label per score, and the preamble names two candidate sources:
adaptive trials' recorded `adaptive_round.payload_text` (precise), and a
coarser static-episode-level proxy (imprecise) -- unless re-deriving the
real per-episode payload turns out to be cheap and deterministic enough to
do properly.

**It does.** Checked directly rather than assumed: `adaptive/sweep.py`'s
own comment already says `attack.attack()` involves "no model call...pure
string [manipulation]", and reading every attack family this project
actually uses (`attacks/families/*.py`, plus AgentDojo's own
`FixedJailbreakAttack`/`ImportantInstructionsAttack` in
`agentdojo/attacks/{base_attacks,important_instructions_attacks,baseline_attacks}.py`)
confirms it: none of them use `random` anywhere in the payload-construction
path this project exercises (`important_instructions`'s *wrong-model-name*
variant does, but this project only ever uses the plain
`important_instructions`, which doesn't). The payload also turns out not
to depend on which model/defense ran the episode: AgentDojo resolves the
`{model}` placeholder by substring-matching `pipeline.name` against a
fixed list of proprietary vendor model IDs, and every provider this
project supports falls through to the same "local model" catch-all
regardless of the specific local/hosted model name (already established in
`agentdojo_adapter.py`'s own comment on this). So the real payload for any
(suite, attack family, user task, injection task) combination is
recomputable via one direct `load_attack(...).attack(user_task,
injection_task)` call, cached per combination -- and this project's real
guard_model/solo-defense data needs only ~15 distinct combinations total,
confirmed by querying the real trace DBs before writing the script
(`SELECT DISTINCT suite, attack, task_id, injection_task_id ...`), not
guessed. Verified live: 0.1s per call, no network/model I/O.

**Ground-truth source, per population, decided and used exactly as
follows:**

- **Adaptive trials** (workspace and MCP): `adaptive_round.payload_text`,
  looked up by the guard-model score's own `episode_id` (1:1 --
  `adaptive_round.episode_id` is unique per round, each round gets its own
  fresh `episode` row). Exact, already-recorded, no re-derivation needed.
- **Static workspace (AgentDojo) attacked episodes**: re-derived via
  `load_attack(resolve_attack_name(run.attack), suite, pipeline).attack(user_task,
  injection_task)`, using `episode.task_id`/`episode.injection_task_id`/
  `run.suite`/`run.attack` to reconstruct the same call `run_episode` made
  live -- `pipeline` here is a bare `SimpleNamespace(name=...)` stand-in
  (only `.name` is ever read off it, for the "local model" catch-all
  above), not a full live `AgentPipeline`. This is the upgrade past the
  originally-anticipated fallback: the checklist's own coarser
  episode-level proxy (label every tool result in an attacked episode
  positive, every one in a benign episode negative) is **not used at
  all** -- every static workspace label is a real per-tool-result
  substring check against the real re-derived payload, the same precision
  the adaptive trials already had.
- **Static/adaptive MCP episodes**: `mcp.poisoned.get_case(injection_task_id).injection_text`
  -- even simpler than the workspace case, since MCP's `PoisonedCase` is a
  fixed dataclass field, not something requiring re-derivation at all.
- **Benign episodes** (`injection_task_id IS NULL`, any suite): label is
  always negative, no case/payload to look up -- there is nothing that
  could be the injection point.

## A real complication found while validating this against live data: YAML reflow

Before trusting a plain Python `in` substring check, it was validated
against real recorded tool results, not assumed to just work. It didn't,
at first: a real, verified example (`tool_call.id=42` in
`runs/local/static_sweep/trace.db`, `important_instructions` /
`user_task_0`) has the real derived payload text nowhere as an exact
substring of the tool result's decoded content, even though the injected
calendar-event description visibly, humanly contains it. The reason,
confirmed by inspection: AgentDojo's workspace environment renders tool
results as `yaml.dump()`-formatted text, and PyYAML's line-folding for
long double-quoted scalars rewraps the payload's own newlines into
YAML's `\<newline><indent>\ ` continuation syntax and re-escapes real
newlines as literal two-character `\n` sequences -- genuinely different
bytes from the original payload, even though a human reading the
rendered YAML sees the same message. Plain whitespace normalization
(`\s+` -> single space) doesn't fix this either, since the reflow isn't
extra *whitespace*, it's YAML's own escape syntax mixed into the text.

**Fix**: `_tool_result_contains` (in the script) tries **both** of two
normalized readings of a tool result and matches against either:
(1) the raw decoded text, whitespace-normalized, for results that are
plain text (most MCP results, error messages, non-YAML content); and
(2) if the text parses as YAML at all, every string leaf of the *parsed*
structure recursively flattened and joined, whitespace-normalized --
`yaml.safe_load` correctly reverses PyYAML's own folding/escaping, so the
original payload text comes back out intact as real characters (verified:
this exact fix turned the `tool_call.id=42` false negative into a
confirmed true match). Explicitly *not* `str(parsed_structure)` -- that
route re-`repr()`s every nested string, which re-escapes real newlines
right back into literal `\n` text and reintroduces the exact problem
being fixed.

## A real structural finding about the MCP suite specifically

`mcp/poisoned.py::PoisonedCase.apply`'s own docstring states the injected
text "can never leak into tool *output*" -- it's appended to a tool's
*description* (part of the tool schema/spec the model sees when choosing
which tool to call), never to a tool call's *result*. `guard_model`'s
`on_tool_result` hook only ever screens tool call results. Checked
directly against real data rather than trusted from the docstring alone:
searching every `tool_call.result_json` row in both
`runs/local/mcp_sweep/trace.db` and `runs/local/adaptive_mcp_sweep/trace.db`
for a substring of any of the 4 real `injection_text` values in this
project's MCP poisoned-case set returns **zero matches**, in either file.

**Consequence, stated plainly rather than silently worked around: MCP-suite
guard-model scores can structurally never be a true positive under this
(or any) tool-*result*-based ground truth.** They're still real,
legitimate negative-class data (does `guard_model` false-positive on
ordinary MCP tool output while an unrelated attack is in progress
elsewhere in the same episode?) and are included in the pooled ROC
population as such -- but every true positive in this project's ROC curve
comes from the workspace suite. This is reported explicitly in
`results/guard_model_roc.md`'s by-source breakdown, not hidden inside a
single pooled number.

## Scope decision: solo `guard_model` only, not composed stacks

S6-04 found a real, repeated mechanism (7/7 matched pairs, same
direction) where composing `spotlighting` ahead of `guard_model` measurably
degrades `guard_model`'s own classification input -- meaning a
`spotlighting+guard_model` score and a solo `guard_model` score are not
measurements of the same thing. Mixing them into one ROC population would
average over two different measurement conditions and produce a curve
that doesn't cleanly describe either one. This ROC curve is restricted to
`run.defense_stack = 'guard_model'` (solo) only, per the consequence
S6-04 itself already flagged for this exact task. Composed-stack scores
exist in the trace DBs and remain available for a future, explicitly
separate contrast -- not attempted here, out of this task's scope.

## Source DBs

Same four trace DBs `generate_adaptive_results.py` already treats as this
project's canonical set: `runs/local/static_sweep/trace.db`,
`runs/local/mcp_sweep/trace.db`, `runs/local/adaptive_sweep/trace.db`,
`runs/local/adaptive_mcp_sweep/trace.db`.
