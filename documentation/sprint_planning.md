# Planning assumptions

---

|  |  |
| --- | --- |
| **Team** | 1 engineer, part-time |
| **Velocity** | ~16 hrs/week (2 weeknights + 1 weekend day) |
| **Sprint length** | 1 week |
| **Total** | Sprint 0 + 7 sprints ≈ 8 weeks, ~128 hrs |
| **Estimates** | Ideal hours. Multiply by 1.4 for anything touching a model API — nondeterminism eats time. |
| **API budget** | **$0 — hard constraint.** Local Ollama on Apple Silicon (16GB+) is the workhorse; free hosted tiers add vendor diversity. See Appendix A, which is the *primary* execution track, not a fallback. |
| **Scarce resource** | Not dollars — **wall-clock hours of local inference** and **daily free-tier quotas**. The grid is sized around these. |
| **Tracking** | GitHub Projects. One issue per task ID. Milestones = sprints. |

**Sprint ritual (solo version).** Monday: pull the sprint's issues, re-estimate, cut anything that doesn't fit. Friday: 20-minute written retro in `docs/retros/` — what shipped, what slipped, what you learned. Those retro notes become your blog post outline and your interview stories. Don't skip them.

**Priority key:** 🔴 blocker for the thesis · 🟡 core · 🟢 nice-to-have, cut first

---

## Epic map

| Epic | Description | Sprints |
| --- | --- | --- |
| **E1 — Harness Core** | Repo, config, trace store, cost instrumentation, caching, CI | 1 |
| **E2 — Defense Layer** | Middleware protocol + 6 prompt/filter/runtime defenses | 1–2 |
| **E3 — Attack Layer** | Static attack registry + adaptive refinement engine | 2, 4 |
| **E4 — MCP Suite** | Mock MCP servers, poisoned schemas, scorers | 3 |
| **E5 — Architectural Defenses** | Dual-LLM, CaMeL-style capability enforcement | 5 |
| **E6 — Experiments** | Static sweep, adaptive sweep, composition matrix, model sweep | 2, 4, 6 |
| **E7 — Analysis & Release** | Pareto plot, selection guide, blog, dataset release | 7 |

---

## Sprint 0 — Groundwork (~8 hrs, can overlap with life)

**Goal:** Know the field well enough that Sprint 1 has no unknowns.

| ID | Task | Est | Priority |
| --- | --- | --- | --- |
| S0-01 | Read AgentDojo paper; take notes on task/injection data model | 2h | 🔴 |
| S0-02 | Read Adaptive Attacks (Zhan et al.) — focus on their adaptive protocol and budget methodology | 1.5h | 🔴 |
| S0-03 | Skim CaMeL, MCPTox, Lessons from Defending Gemini | 2h | 🟡 |
| S0-04 | `pip install agentdojo`, run the example benchmark end-to-end on one cheap model | 1.5h | 🔴 |
| S0-05 | Set up API keys, spend alerts, and a cost dashboard | 1h | 🔴 |

**Exit criteria:** AgentDojo runs locally; you can explain its task/injection/attack abstractions from memory; spend alerts are live.

---

## Sprint 1 — Harness Core (E1, E2 partial)

**Sprint goal:** Any defense can be plugged in, any run is fully traced, and a published baseline is reproduced.

> This is the highest-leverage sprint. Everything downstream reads from the trace store and implements the `Defense` protocol. Rushing it costs you Sprint 6.
> 

| ID | Task | Est | Priority | Deps |
| --- | --- | --- | --- | --- |
| S1-01 | Repo scaffold: `pyproject.toml`, package layout, ruff + mypy, pytest, GitHub Actions CI | 2h | 🔴 | — |
| S1-02 | **`Defense` protocol** — define the 4 hooks (`on_pre_generate`, `on_tool_result`, `on_pre_tool_call`, `cost`) plus `DefenseStack` for ordered composition | 3h | 🔴 | S1-01 |
| S1-03 | **Trace schema** — SQLite tables: `run`, `episode`, `step`, `tool_call`, `defense_event`, `cost_record`. One row per event, fully reconstructable episode. | 3h | 🔴 | S1-01 |
| S1-04 | **Model client wrapper** — uniform interface across vendors; capture tokens in/out, wall-clock ms, computed $ per call | 2.5h | 🔴 | S1-03 |
| S1-05 | **Response cache** — content-addressed on (model, messages, params, seed); disk-backed; `--no-cache` escape hatch | 2h | 🔴 | S1-04 |
| S1-06 | **Config system** — YAML experiment configs declaring models × defenses × suites × attacks; one command runs one config | 2h | 🟡 | S1-02 |
| S1-07 | AgentDojo integration adapter — wrap their task suites so your runner drives them | 3h | 🔴 | S1-02 |
| S1-08 | **Reproduce a published AgentDojo number** on one model + one attack; document the delta in `docs/reproduction.md` | 2.5h | 🔴 | S1-07 |
| S1-09 | `NoDefense` baseline implementing the protocol (proves the interface) | 0.5h | 🔴 | S1-02 |
| S1-10 | Unit tests for trace integrity + cost accounting | 1.5h | 🟡 | S1-03 |

**Acceptance criteria**

- `python -m injection_pareto run configs/smoke.yaml` completes and writes a queryable trace DB
- A SQL query returns total $ and p95 latency per episode
- `docs/reproduction.md` shows your ASR vs. the paper's, with any gap explained
- Re-running an identical config with cache hits costs $0

**Risks:** AgentDojo's API is explicitly marked unstable — pin the version in `pyproject.toml` on day one and never float it.

---

## Sprint 2 — Defenses 1–6 + Static Baseline (E2, E3, E6)

**Sprint goal:** Six defenses implemented, five attack families registered, first full static results table.

| ID | Task | Est | Priority | Deps |
| --- | --- | --- | --- | --- |
| S2-01 | **Attack registry** — `Attack` protocol + injection-point resolution (where in a tool result the payload lands) | 2h | 🔴 | S1-02 |
| S2-02 | Implement 5 static attack families: naive, ignore-previous, important-instructions, context-completion, encoding/obfuscation | 3h | 🔴 | S2-01 |
| S2-03 | **D2 Spotlighting** — delimiting + datamarking of untrusted tool output | 1.5h | 🔴 | S1-02 |
| S2-04 | **D3 Instructional prevention** — system-prompt hardening variant | 1h | 🔴 | S1-02 |
| S2-05 | **D4 Guard-model classifier** — screen every tool result; log score + threshold; emit `defense_event` | 3h | 🔴 | S1-04 |
| S2-06 | **D5 Canary / known-answer detection** — canary token in a probe instruction, detect if the model's answer diverges | 2.5h | 🟡 | S1-02 |
| S2-07 | **D6 Tool allowlist + argument policy** — per-task allowed tools, argument constraints (e.g. recipient must appear in the user's original request) | 3h | 🔴 | S1-02 |
| S2-08 | **Security scorer** — did the injected task complete? Add `partial_compromise` label for steps-toward-goal | 2h | 🔴 | S1-03 |
| S2-09 | **Utility scorer** — benign task completion, defense on vs. off, same task set | 2h | 🔴 | S1-03 |
| S2-10 | **Sweep runner** — cartesian product over config, resumable, parallel, progress bar | 2.5h | 🔴 | S1-06 |
| S2-11 | Run static sweep: 6 defenses × 5 attacks × 3 models × AgentDojo suites | 3h wall | 🔴 | S2-10 |
| S2-12 | `results/static_baseline.md` — first results table, auto-generated from traces | 1.5h | 🟡 | S2-11 |

**Acceptance criteria**

- Every defense reports its own cost via `cost()`; overhead is separable from base agent cost in the DB
- Security and utility both measured for all 6 defenses on the same task set
- Guard model emits a full score distribution, not just a binary — you need it for the ROC later
- Results table regenerates from traces with one command (never hand-copy a number)

**Watch for:** the utility scorer must run on **injection-free** episodes. If you measure utility on attacked runs, every number in the project is wrong. Write that assertion into a test.

---

## Sprint 3 — MCP Tool-Poisoning Suite (E4)

**Sprint goal:** A novel attack surface AgentDojo doesn't cover, with the same defense sweep run against it.

> This is your differentiator. Injection enters through the *tool schema*, not the data — a trust path most defenses never guard.
> 

| ID | Task | Est | Priority | Deps |
| --- | --- | --- | --- | --- |
| S3-01 | **Mock MCP server framework** — declare a server as a YAML/py spec (tools, schemas, canned responses); fully sandboxed, no network | 4h | 🔴 | S1-07 |
| S3-02 | Author **15 mock servers** across realistic domains: file storage, ticketing, CRM, calendar, payments, code search, analytics… | 4h | 🔴 | S3-01 |
| S3-03 | Author **~15 benign user tasks** requiring multi-tool use across those servers (the utility baseline for this suite) | 3h | 🔴 | S3-02 |
| S3-04 | Design **~40 poisoned-description cases** across 4 sub-families: direct instruction in description, fake usage-note, fake required-precondition, cross-tool redirection | 4h | 🔴 | S3-02 |
| S3-05 | Injection-task definitions + security scorer for the MCP suite | 2h | 🔴 | S3-04, S2-08 |
| S3-06 | Register suite with the sweep runner; run full 6-defense × 4-sub-family × 3-model sweep | 2h + wall | 🔴 | S3-05, S2-10 |
| S3-07 | `results/mcp_suite.md` + written analysis of where prompt-level defenses under-perform vs. the data-path suites | 2h | 🟡 | S3-06 |

**Acceptance criteria**

- No mock server makes a real network call — enforced by a test that fails if `socket` is touched
- Benign task completion on the MCP suite is ≥70% undefended (otherwise your tasks are too hard and utility deltas will be noise)
- Documented comparison: defense effectiveness on schema-path vs. data-path attacks

**Hypothesis to state up front (and let the data judge):** spotlighting and delimiting will underperform here because they mark untrusted *data*, while the poisoned content arrives as trusted *schema*.

---

## Sprint 4 — Adaptive Attack Engine (E3, E6)

**Sprint goal:** Claim C1 — show the static ranking is misleading.

| ID | Task | Est | Priority | Deps |
| --- | --- | --- | --- | --- |
| S4-01 | **Feedback channel** — after each attempt, expose only: injected-task success (bool), defense intervened (bool), agent's visible refusal text | 2h | 🔴 | S2-08 |
| S4-02 | **Mutation engine** — LLM-driven payload rewriting, constrained to stay within its attack family | 4h | 🔴 | S4-01 |
| S4-03 | **Budget controller** — hard cap N=20 rounds per (defense, attack, task); log every round; identical budget for every pair | 2h | 🔴 | S4-02 |
| S4-04 | Early-stop on success + record `rounds_to_success` (a richer signal than binary ASR) | 1h | 🟡 | S4-03 |
| S4-05 | Run adaptive sweep across all 6 defenses × 5 families × 2 models (cost-capped) | 4h + wall | 🔴 | S4-03 |
| S4-06 | Run adaptive sweep on the MCP suite | 2h + wall | 🟡 | S4-05, S3-06 |
| S4-07 | **Reordering analysis** — ASR@1 vs ASR@20 table, rank-correlation between static and adaptive orderings | 2.5h | 🔴 | S4-05 |
| S4-08 | Qualitative writeup: for each broken defense, what the successful mutation exploited | 2h | 🟡 | S4-07 |

**Acceptance criteria**

- Every (defense, attack) pair received exactly the same refinement budget — asserted in code, stated in the README
- `results/adaptive.md` reports ASR@1, ASR@20, and rounds-to-success distribution
- At least one defense's rank changes materially between static and adaptive — and if none does, that's a real finding too; report it honestly

**Cost risk — the big one.** 6 defenses × 5 attacks × 20 rounds × tasks × models multiplies fast. Before launching S4-05, run a 1% sample and extrapolate the bill. Cut models before you cut rounds; rounds are the scientific claim.

---

## Sprint 5 — Architectural Defenses (E5)

**Sprint goal:** The two defenses that are hard to implement and where the interesting utility tax lives.

| ID | Task | Est | Priority | Deps |
| --- | --- | --- | --- | --- |
| S5-01 | **D7 Dual-LLM / quarantined LLM** — privileged planner with tool access; quarantined reader processes untrusted content and returns only structured, non-instructional summaries | 5h | 🔴 | S1-02 |
| S5-02 | D7 utility measurement + failure analysis (where does quarantining break legitimate tasks?) | 2h | 🔴 | S5-01 |
| S5-03 | **D8 CaMeL-style capability enforcement** — planner emits a restricted program; values carry provenance tags; policy engine gates tool calls on data flow | 8h | 🔴 | S5-01 |
| S5-04 | Policy definitions per suite (what may flow to which sink) | 2.5h | 🔴 | S5-03 |
| S5-05 | Run static + adaptive sweeps for D7 and D8 | 2h + wall | 🔴 | S5-03, S4-03 |
| S5-06 | Latency and $/task breakdown for architectural defenses (extra model calls per task) | 1.5h | 🟡 | S5-05 |

**Acceptance criteria**

- D8 blocks a documented data-exfiltration case *by policy*, not by classification — demonstrate with a trace
- Utility tax quantified for both, on the same task set as every other defense
- Extra model calls per task recorded

**Scope valve:** S5-03 is the single riskiest task in the plan. If it's blowing past estimate by Wednesday, ship a **reduced CaMeL** — provenance tagging plus a policy check on 2–3 high-risk sink tools only — and document the simplification honestly. A partial implementation with clear boundaries beats a sprint overrun. Do not let this eat Sprint 6.

---

## Sprint 6 — Composition + Model Sweep (E6)

**Sprint goal:** Claims C2 and C3 — the frontier and the composition finding.

| ID | Task | Est | Priority | Deps |
| --- | --- | --- | --- | --- |
| S6-01 | **Composition runner** — `DefenseStack` executing ordered pairs, cost attributed per layer | 2h | 🔴 | S1-02 |
| S6-02 | Run all pairs of the top-4 defenses, static + adaptive | 3h + wall | 🔴 | S6-01 |
| S6-03 | **Independence analysis** — observed joint ASR vs. `ASR_A × ASR_B` prediction; flag correlated blind spots | 2.5h | 🔴 | S6-02 |
| S6-04 | Blind-spot qualitative analysis: name the shared failure mode for the worst-composing pair | 2h | 🟡 | S6-03 |
| S6-05 | Expand model sweep to 6 models (2 frontier, 2 mid, 2 open-weight) on the core config | 3h + wall | 🔴 | S2-11 |
| S6-06 | **Capability-vs-vulnerability analysis** — test the MCPTox finding independently on your suites | 2h | 🟡 | S6-05 |
| S6-07 | Guard-model **ROC curve** across thresholds (detection vs. false-positive) | 2h | 🟡 | S2-05 |

**Acceptance criteria**

- Composition matrix complete for top-4 pairs with observed-vs-predicted deltas
- At least one pair shown to underperform independence assumptions, with a named mechanism
- Cross-vendor results — a single-vendor finding will be dismissed

---

## Sprint 7 — Analysis & Release (E7)

**Sprint goal:** Turn results into the artifact people link to.

| ID | Task | Est | Priority | Deps |
| --- | --- | --- | --- | --- |
| S7-01 | **Pareto plot** — adaptive ASR × utility retention, bubble = $/task, frontier highlighted | 3h | 🔴 | S6-02 |
| S7-02 | **Defense selection guide** table — the headline deliverable | 2h | 🔴 | S7-01 |
| S7-03 | README: results first, method second, reproduction third. Pareto plot above the fold. | 3h | 🔴 | S7-02 |
| S7-04 | `make reproduce` — one command, cached artifacts, runs the headline config | 2h | 🔴 | S1-05 |
| S7-05 | Release MCP poisoning suite as a standalone dataset (HuggingFace) with a datasheet | 2.5h | 🟡 | S3-04 |
| S7-06 | **Blog post** — lead with the three claims, not the architecture | 4h | 🔴 | S7-02 |
| S7-07 | 60-second demo GIF: an attack succeeding undefended, then blocked by D8 | 1.5h | 🟡 | S5-03 |
| S7-08 | Limitations + responsible-disclosure section | 1.5h | 🔴 | — |
| S7-09 | Resume bullets with final real numbers | 0.5h | 🟡 | S7-02 |

**Acceptance criteria**

- A stranger can reproduce the headline table from a clean clone
- README's first screenful contains a number, a chart, and the three claims
- Limitations section names at least four honest weaknesses (budget-limited adaptivity, mock environments, model version drift, single-attacker-model bias)

---

## Definition of Done (every task)

1. Code merged with type hints, passing ruff + mypy
2. Any new defense/attack/suite registered and covered by at least one test
3. All results written to the trace DB — **never** a number hand-copied into markdown
4. Tables and figures regenerate from traces via script
5. Anything surprising captured in `docs/retros/` the day you find it

---

## Risk register

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| API spend overruns budget | High | High | Cache from Sprint 1; 1% sample-and-extrapolate before every sweep; hard spend alerts |
| CaMeL (S5-03) overruns | High | Med | Pre-agreed reduced scope; hard Wednesday checkpoint |
| AgentDojo API changes | Med | High | Pin version at Sprint 1, never float |
| Adaptive loop finds nothing | Low | High | Even a null result is publishable — but validate the loop early on the *undefended* baseline, where it must succeed |
| Utility scored on attacked runs | Med | Critical | Assertion test in S2-09; this silently invalidates everything |
| Mock MCP tasks too hard → noisy utility | Med | Med | ≥70% undefended completion gate in S3 acceptance |
| Scope creep into new environments | Med | Med | Suites are frozen after Sprint 3 |

---

## Cut list (in order, if you fall behind)

1. S6-07 guard-model ROC
2. S4-06 adaptive on MCP suite
3. S6-06 capability-vs-vulnerability analysis
4. S2-06 canary detection (D5)
5. S7-05 dataset release
6. S5-03 → reduced CaMeL scope

**Never cut:** S1-08 (reproduction), S2-09 (utility scorer), S4-03 (budget controller), S7-01 (Pareto plot). Those four carry the entire thesis.

---

## Milestone checkpoints

| End of | You should be able to say |
| --- | --- |
| Sprint 1 | "I reproduced AgentDojo's published baseline and instrumented every dollar and millisecond." |
| Sprint 2 | "I have six defenses measured on both security and utility." |
| Sprint 3 | "I built an attack surface the reference benchmark doesn't cover." |
| Sprint 4 | "Adaptive attacks reorder the published defense ranking." |
| Sprint 5 | "I implemented capability-based enforcement and measured what it costs." |
| Sprint 6 | "Stacked defenses underperform independence assumptions, and I know why." |
| Sprint 7 | "Here's the chart that tells you what to ship." |

If you can say the Sprint 4 line, you already have a portfolio project worth talking about. Everything after deepens it.

---

# Appendix A — The $0 Execution Track (primary)

**Confirmed setup:** Apple Silicon, 16GB+ unified memory. Free hosted tiers in bounds. Zero dollars.

This is the plan of record. Everything in the sprints above is sized against the constraints here.

## The argument for doing it this way anyway

Frontier API models drift, get silently updated, and get deprecated. A result you produce today against a hosted frontier model **may be unreproducible in six months** — a genuine and widely-acknowledged weakness in agent security papers.

Open-weight models pinned by version and quantization hash are reproducible forever. Put that in the README as a design choice, not an apology:

> All headline results use open-weight models pinned by hash, so this benchmark remains reproducible after hosted models are deprecated.
> 

That framing is defensible in an interview and turns a budget constraint into a methodology decision.

## A.1 — The model ladder

Six models, zero dollars, spanning a genuine capability range. The ladder *is* your S6-06 capability analysis, and it's cleaner than mixing opaque hosted versions.

| Tier | Model class | Source | Role |
| --- | --- | --- | --- |
| L1 | ~3–4B instruct, Q4 | Ollama, local | Cheap sanity runs; fast iteration during dev |
| L2 | ~7–8B instruct, Q4 (~5GB) | Ollama, local | **Primary workhorse.** Most sweep episodes run here. |
| L3 | ~12–14B instruct, Q4 (~9GB) | Ollama, local | Upper local rung; slower, use for headline configs |
| L4 | Gemini Flash-class | Google AI Studio free tier | Hosted mid-tier, different vendor |
| L5 | Llama 3.3 70B | Groq free tier | Large open-weight; the top of your ladder |
| L6 | Rotating free model | OpenRouter free tier | Vendor breadth on the headline config only |

**Selection criterion that matters more than size: pick models explicitly tuned for tool calling.** Injection benchmarks require the agent to actually use tools competently. A model that can't reliably emit valid tool calls produces noise, not results.

Pin every local model by digest, not tag — `ollama` tags get republished. Record digests in `configs/models.yaml`.

## A.2 — Throughput math (do this before committing to a grid)

On Apple Silicon with Metal, an 8B Q4 model runs roughly 20–40 tok/s. Working estimate:

```
1 episode ≈ 6 model calls ≈ 3,000 generated tokens
        ≈ 100–150 seconds at L2
1 overnight run (10h) ≈ 250–350 episodes at L2
                      ≈ 120–180 episodes at L3
```

Sizing the grid against that:

| Sweep | Config | Episodes | Nights (L2) |
| --- | --- | --- | --- |
| Static, AgentDojo | 6 def × 5 atk × 12 tasks | 360 | ~1.5 |
| Utility baseline | 6 def × 12 tasks, benign | 72 | ~0.3 |
| MCP suite | 6 def × 4 sub-fam × 10 tasks | 240 | ~1 |
| Adaptive | 6 def × 5 atk × 6 tasks × ≤10 rounds | ≤1,800 worst case | 5–7 |
| Architectural (D7/D8) | 2 def × 5 atk × 12 tasks, ×2–3 calls each | 120 (heavy) | ~1.5 |
| Composition | 6 pairs × 5 atk × 8 tasks | 240 | ~1 |

**Adaptive is 60% of your total compute.** Early-stop on success helps enormously in practice — the undefended baseline succeeds in round 1 — so worst case rarely materializes. But measure actual round counts after your first adaptive run and re-plan.

**Required grid reductions vs. the paid plan:**

- Tasks per suite: **12 stratified** rather than the full AgentDojo set. Stratify by tool count and domain; document the sampling method.
- Adaptive budget: **N=10** for the full grid, **N=20** on one documented subset. State both explicitly — comparability across defenses matters far more than the absolute number.
- Adaptive model coverage: **L2 + L5 only**.
- Mutation engine and LLM judge both run on **L2 locally** — free, and keeps the attacker's capability constant across defenses, which is methodologically cleaner anyway.

## A.3 — Free-tier allowances

Approximate, and they change often — verify before planning a sweep around them.

| Provider | Free allowance | Binding constraint |
| --- | --- | --- |
| Google AI Studio (Gemini) | ~1,500 requests/day, no credit card | Requests/day |
| Groq | ~1,000 req/day, ~100K tokens/day | **Token cap binds first** — budget ~30 episodes/day |
| OpenRouter | 30+ free models, ~50 req/day pre-credit | Very low volume; headline configs only |
| Kaggle | ~30 GPU-hrs/week, P100, 12h sessions | Optional; only if you want a larger model than L3 |

Groq's 100K tokens/day is the tight one — at ~3K tokens/episode that's ~30 episodes daily. Plan L5 runs as a slow trickle across the whole project rather than a burst.

## A.4 — Plan deltas

| Item | Change |
| --- | --- |
| S1-05 cache | Promoted to **existential**. A cache miss now costs a night, not a dollar. Content-address on model digest too. |
| **New — S1-11 Run queue** (~3h, 🔴) | Persistent SQLite-backed job queue: quota-aware scheduling, rate-limit backoff, resume-on-crash, per-provider daily budgets. Without this, a 3am failure wastes the whole night. |
| **New — S1-12 Baseline utility gate** (~2h, 🔴) | Before anything else: measure undefended benign task completion for L2 and L3. **If L2 is below ~50%, your utility-tax deltas are noise.** Fix by swapping models or restricting to a task subset your models can actually do — and document the restriction. |
| **New — S1-13 Ollama harness** (~2h, 🟡) | Model digest pinning, warm-up, concurrency tuning, deterministic seeds where supported. |
| S2-05 guard model | Runs on L1 locally — a small classifier-grade model is the realistic deployment choice anyway. |
| S4-02 mutation engine | L2 local. Fixed attacker capability across all defenses; state this as a methodological choice. |
| S5-01 dual-LLM | Quarantined reader on L1, privileged planner on L2. Cheap and architecturally faithful. |
| S6-05 model sweep | L1–L5, headline config only. Genuinely cross-vendor and cross-capability. |
| All sweeps | Kick off before bed. Friday nights are your long runs. Sprint boundaries should land on a Monday so a weekend run feeds Monday's analysis. |

## A.5 — The honest risk this introduces

**Small models may compress your dynamic range.** MCPTox found more capable models are often *more* vulnerable to injection, because the attack exploits instruction-following competence. If L1–L3 are simply bad at following *any* instruction, attack success may look artificially low and defenses artificially effective.

This is the one methodological threat that money would have solved. Three mitigations:

1. **L5 (Llama 3.3 70B via Groq) is non-negotiable** for the headline config. It anchors the top of your ladder and is where you check whether the capability/vulnerability relationship holds.
2. **Report the capability-vulnerability curve explicitly** (S6-06). If ASR rises monotonically with model capability across L1→L5, you've independently replicated a published finding on a new suite — that's a *result*, not a limitation.
3. **Name it in Limitations.** "Results are anchored on open-weight models up to 70B; frontier-model behavior is extrapolated, not measured." Reviewers respect stated limits far more than they penalize them.

## A.6 — The reproducibility argument

Frontier API models drift, get silently updated, and get deprecated — results against them may be unreproducible within months, a widely acknowledged weakness in agent security work. Open-weight models pinned by digest are reproducible indefinitely.

Put this in the README as a design choice, not an apology:

> All results use open-weight models pinned by digest, so this benchmark remains reproducible after hosted models are deprecated. `make reproduce` regenerates every table from scratch.
> 

That is a strong interview answer. The constraint produced a better methodology.

**Bottom line:** claims C1, C2, and C3 are all fully provable at $0. What money would have bought is one more row in the model table and about three weeks of wall-clock time.