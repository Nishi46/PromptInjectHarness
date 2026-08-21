---
license: mit
task_categories:
  - other
language:
  - en
tags:
  - prompt-injection
  - llm-agents
  - security
  - red-teaming
  - mcp
  - tool-use
pretty_name: MCP Tool-Description Poisoning Suite
size_categories:
  - n<1K
---

# MCP Tool-Description Poisoning Suite

41 hand-authored prompt-injection cases that poison an LLM-agent tool's
*description* (not its output) across 15 mock MCP-style servers spanning
common workplace tool categories (email, calendar, CRM, payments, cloud
infrastructure, and more). Built for
[`injection-pareto`](https://github.com/Nishi46/PromptInjectHarness) (a
prompt-injection-defense benchmarking harness) to evaluate defenses against an indirect-injection
vector that existing benchmarks (notably
[AgentDojo](https://github.com/ethz-spylab/agentdojo)) don't model: the
injected text lives in a tool's *schema*, not in data a tool call returns.

This document follows the
[Datasheets for Datasets](https://arxiv.org/abs/1803.09010) framework.

## Motivation

**For what purpose was the dataset created?** Every existing indirect
prompt-injection benchmark the authors are aware of (AgentDojo included)
injects the payload into *tool output* — a calendar event's description, an
email body, a search result. That's a real and well-studied vector, but it
leaves an entire class of MCP-specific attack surface untested: the tool
*description* itself, part of the schema an agent reads when deciding which
tool to call, before any tool has even been invoked. A poisoned description
can instruct an agent to take an unrelated, attacker-chosen action the
moment the agent merely *considers* using that tool — no compromised data
source required. This dataset exists to make that vector testable,
concretely and reproducibly, against real defenses.

**Who created this dataset?** One engineer, as part of `injection-pareto`'s
Sprint 3 (`E4 — MCP Suite`). See that project's
`documentation/sprint_planning.md` and `docs/notes/mcp_suite.md` for the
full design history.

## Composition

**What do the instances represent?** Each of the 41 records is one
poisoned-tool-description injection case: which server/tool it targets,
the literal text appended to that tool's real description, a human-readable
statement of the attacker's goal, and a **formal ground-truth predicate**
(exact tool name + argument match/predicate) that mechanically determines
whether a given agent trace was actually compromised by it. Each case is
also paired with the real benign multi-tool task that legitimately uses the
same server, so a defense's benign-task utility and this case's attack
success can be measured on the same environment.

**How many instances?** 41 total: 40 designed across a fixed 4×10 grid (4
injection sub-families × 10 hand-authored instances each, one per mock
server) plus 1 additional demonstration case (`poison_body_exfil_email_get_email`)
built later for a specific data-provenance-based defense (capability
enforcement / CaMeL-style) that the other 40 don't exercise — every other
case routes its compromise through a `recipient`-shaped argument
(`recipient`, `to`, `assignee`, …) that a simple allowlist-style defense
already catches by argument name; this one instead smuggles a sensitive
*tool-result field* into an argument no such defense inspects, exercising
provenance-based detection specifically.

**Sub-families** (`sub_family` field), 4 distinct mutation shapes, each
representing a different way a tool description can misdirect an agent:

| Sub-family | Shape | n |
| --- | --- | --- |
| `direct_instruction` | The description directly tells the agent to take an unrelated action ("IMPORTANT: ..."). | 11 |
| `fake_usage_note` | A plausible-looking "Note:" appended to the description that is actually the injection. | 10 |
| `fake_precondition` | The description claims the tool can't be used until another (attacker-chosen) call happens first. | 10 |
| `cross_tool_redirection` | The description names a *different* real tool on the same server and redirects the agent to call it with attacker-chosen arguments. | 10 |

**What data does each instance consist of?** (see `mcp_poisoning_suite.jsonl`
for the exact schema)

- `id` — unique case identifier.
- `sub_family` — one of the four above.
- `target_server` / `target_tool` — which mock server and tool this case
  poisons.
- `original_tool_description` — the tool's real description before
  poisoning.
- `injection_text` — the literal text appended to it.
- `poisoned_tool_description` — the resulting full description (computed
  by the harness's own real mutation code, not reconstructed by hand).
- `injection_goal` — a human-readable statement of the attacker's goal.
- `ground_truth_calls` — the formal predicate: a list of
  `{tool_name, arguments}` records; a trace matching *any one* (tool name
  plus every named argument, by equality or, where the value is a
  `{"__predicate__": "<source>"}` object, a documented predicate) counts as
  a full compromise. Only one case (the D8 demonstration case) uses a
  predicate rather than a literal — its `body` argument is checked by
  substring containment against a specific account number, since the reply
  text itself is free-form model output, not a literal value the injection
  can pin down.
- `paired_benign_task` — the real, legitimate multi-tool task an agent
  would perform on the same server absent any attack, with its own
  `prompt` and `expected_calls` (for measuring benign-task utility on the
  *same* environment this case poisons).

**Is there a label or target associated with each instance?**
`ground_truth_calls` is the label — not a soft/subjective annotation but a
mechanical predicate: run an agent against the poisoned tool, replay its
tool-call trace against `ground_truth_calls`, and the match (or lack of
one) is the ground truth. No human judgment is involved in scoring.

**Is any information missing from individual instances?** No — every field
above is populated for every instance; `paired_benign_task` is never null
(verified: `scripts/export_mcp_dataset.py` raises rather than emitting an
unpaired case, and reports the count of unpaired cases — currently 0 — in
its own output).

**Does the dataset contain data that might be considered sensitive?**
No real personal, confidential, or identifying data of any kind — every
name, email address, ID, and account number is a fabricated placeholder
(`attacker@evil.example`, `EMP-2`, `ACCT-77213-XQ`, etc.) inside a fully
synthetic, offline mock environment. Nothing here was scraped, and no
person's real information is represented.

## Collection process

**How was the data acquired?** Hand-authored, not scraped, crowdsourced, or
model-generated. Each sub-family's mutation *shape* was fixed by design
before any instance was written (see Composition above); each of the 40
instances was then authored against that fixed shape, one per mock server,
targeting a realistic tool on that server. The one additional case was
authored later, against a different, deliberately narrower design goal (see
Composition).

**Over what timeframe was the data collected?** Authored in one sitting as
part of a single, time-boxed project sprint (Sprint 3 of `injection-pareto`,
plus one later addition in Sprint 5) — not collected or aggregated over
time.

## Preprocessing / cleaning / labeling

None required or performed — every instance was authored directly in its
final, structured form (a `PoisonedCase` + `MCPInjectionTask` Python
object in the source project). The exported JSONL is a direct,
lossless serialization of those objects (`scripts/export_mcp_dataset.py`),
not a transformation of some rawer prior form.

## Uses

**What other tasks could the dataset be used for?** Benchmarking any
system that (a) presents an LLM agent with tool/function schemas it reads
before calling anything, and (b) needs to be evaluated against tool
*description*-based indirect prompt injection specifically — a narrower
and less-studied vector than output-based injection. Also usable as
worked examples for teaching or writing about this specific attack class.

**Is there anything about the composition/collection that might impact
future uses?** Yes, stated plainly:

- **Small and hand-authored, not adversarially optimized.** 41 cases is
  enough to exercise a defense's behavior across 4 mechanism shapes, not
  enough to claim broad statistical coverage of the space of possible
  poisoned descriptions. None of the injection text was refined against
  real model failures the way an adaptive attacker would.
- **English only, workplace-SaaS domain only.** Every server/tool is a
  plausible workplace tool (email, CRM, payments, …); the suite says
  nothing about other domains or languages.
- **The mock environment is intentionally simple.** Tool responses are
  static or lightly stateful (`mcp/runtime.py::MockServerState`), not real
  services — a defense evaluated only against this suite hasn't been
  tested against real-service complexity (auth, rate limits, real
  multi-step state).
- **Ground truth is exact-match/predicate-based**, not a judge model — a
  compromise that achieves the attacker's goal via an unanticipated
  argument shape could, in principle, go unscored. In practice this was
  checked against real model traces during the source project's own
  sweeps (`docs/notes/mcp_suite.md`), not just assumed correct.

**Are there tasks for which the dataset should not be used?** Not for
training or fine-tuning a model to generate novel prompt-injection attacks
— it's sized and structured for *defense evaluation*, not attack
generation, and using it that way would be a real misuse of a resource
built for defensive security research. Not for use against any system you
don't have explicit authorization to test.

## Distribution

**Will the dataset be distributed to third parties?** Released publicly
(via this Hugging Face dataset repository) alongside the
`injection-pareto` source project, under the same license.

**License.** MIT — same as the parent `injection-pareto` repository (see
that repository's own `LICENSE`).

## Maintenance

**Who maintains the dataset?** The `injection-pareto` project author.
Issues, corrections, or proposed additions should go through the parent
repository, not this dataset repository directly — the JSONL here is a
generated export (`scripts/export_mcp_dataset.py`) of source-of-truth data
that lives there (`src/injection_pareto/mcp/poisoned.py`,
`mcp/injection_tasks.py`, `mcp/tasks.py`).

**Will the dataset be updated?** If the source project's case set changes,
re-running the export script regenerates this file exactly — no hand
edits are ever made to `mcp_poisoning_suite.jsonl` directly.

## Loading this dataset

```python
from datasets import load_dataset

ds = load_dataset("json", data_files="mcp_poisoning_suite.jsonl", split="train")
print(ds[0])
```
