# Agent Prompt-Injection Defense Harness — Build Scope

**Project :** `injection-pareto` — Head-to-head evaluation of prompt-injection defenses for tool-using agents, under adaptive attack, measured on security *and* utility *and* cost.

**Target:** 5–7 weeks part-time. A 3-week subset is marked ⚡.

---

## 0. Correction: this space is not under-served, and that changes the project

I told you agent security benchmarking was under-served. On the academic side that was wrong. Existing work:

| Work | What it is |
| --- | --- |
| **AgentDojo** (ETH Zurich, 2024) | The reference benchmark. 97 tasks, 629 security cases, 4 domains (workspace, Slack, banking, travel). Extensible, `pip install agentdojo`. |
| **InjecAgent** (UIUC, 2024) | 1,054 cases, 17 user tools, 62 attacker tools. ReAct GPT-4 compromised ~24% of the time. |
| **MCPTox** (2025) | Tool-poisoning against 45 live MCP servers, 353 real tools. |
| **MCPSecBench**, **AgentDyn**, **AgentLAB** | MCP protocol surfaces, dynamic environments, long-horizon attacks. |
| **CaMeL** (DeepMind), **Spotlighting**, **LlamaFirewall**, **MELON**, **IPIGuard**, **PromptArmor** | The defense side. |

**Building "a prompt injection benchmark" from scratch now would be a mistake** — you'd spend six weeks rebuilding AgentDojo worse, and any reviewer who knows the field would notice.

**But the real gap is sharper and more valuable.** Three findings define it:

1. **Zhan et al. (NAACL 2025) evaluated 8 published defenses and broke all of them with adaptive attacks, holding >50% attack success.** Most defenses are still published with static-attack numbers.
2. **CaMeL achieves provable security but drops AgentDojo task completion from 84% → 77%.** That utility tax is the actual deployment blocker, and it's rarely measured alongside latency and dollar cost.
3. **MCPTox found more capable models are often *more* vulnerable** — o1-mini hit 72.8% attack success — because injection exploits the same instruction-following that makes them useful.

So: the literature is full of defenses proposed in isolation, evaluated against static attacks, with utility cost reported inconsistently and latency/dollar cost almost never. **An engineer choosing what to actually ship has no data.**

**Your project is that missing artifact:** a uniform harness that implements 7–8 defenses, attacks each *adaptively*, measures the security/utility/cost frontier, tests defense *composition*, and ships a practitioner selection guide.

This is a better project than a new benchmark. It's differentiated, it builds credibly on prior art instead of ignoring it, and it answers a question a hiring manager at any agent-shipping company has personally been stuck on.

---

## 1. Thesis

> Every published defense reports a security number. Almost none report what it costs you. This harness measures the full tradeoff — under an attacker that knows the defense is there.
> 

Three claims you should be able to defend by the end:

- **C1 — The static/adaptive gap.** Ranking defenses by static attack success gives a materially different order than ranking by adaptive attack success. Quantify the reordering.
- **C2 — The Pareto frontier.** Plot security vs. utility retention, with latency and $/task as annotations. Most defenses are dominated. Show which.
- **C3 — Composition is not additive.** Stacking two defenses that each block 60% does not yield 84%. Their failure modes correlate. Measure it.

Any one of these three is a blog post. All three is a project people link to.

---

## 2. Safety and scope guardrails — read first

This is defensive security research, and it needs to look like it from commit one. Reviewers will judge your judgment here as much as your code.

- **Sandboxed mock environments only.** AgentDojo's tools are simulated — no real email sent, no real money moved, no real systems touched. Keep it that way. Every new environment you add is mock-backed.
- **No live production targets.** Do not point this at real MCP servers, real SaaS accounts, or anyone's deployment. MCPTox did live-server testing under a research protocol; you don't need to, and doing it casually would be a red flag rather than a credential.
- **Attack payloads at literature level.** Use and adapt the attack families already published. Your contribution is the *measurement methodology*, not novel weaponization. Don't publish optimized payloads tuned against a specific vendor's shipped product.
- **Responsible disclosure.** If you find a genuinely novel failure against a commercial model or product, disclose to the vendor before publishing, and say so in the README. A "Disclosure" section signals maturity.
- **Frame everything as defense evaluation.** README title, abstract, and commit messages. "Which defenses actually hold up" — not "how to hijack agents."

Say this out loud in interviews too. Security-adjacent work with visible judgment is a strong signal; the same work without it reads as a liability.

---

## 3. Scope boundaries

**In**

- AgentDojo as the substrate — its 4 domains, its task/injection structure, its utility scoring ⚡
- One new domain you contribute: **MCP tool-description poisoning** (the surface AgentDojo doesn't cover)
- 7–8 defenses implemented behind one uniform interface ⚡
- Static attack baseline, then adaptive attacks per defense
- Composition matrix for the top 4 defenses
- Full cost instrumentation: latency, tokens, $/task
- 5–6 models spanning capability tiers and vendors

**Out**

- New base benchmark environments beyond the MCP suite (use AgentDojo's)
- Gradient-based / white-box attack optimization (black-box adaptive only — cheaper and more realistic)
- Training a defense model
- Multi-agent or long-horizon attack scenarios (that's AgentLAB's territory)

---

## 4. Architecture

```
                  ┌──────────────────────────────┐
                  │  Task Suites                 │
                  │  AgentDojo: workspace, slack,│
                  │  banking, travel             │
                  │  + NEW: mcp-registry suite   │
                  └──────────────┬───────────────┘
                                 │
     ┌───────────────────────────┼───────────────────────────┐
     │                           │                           │
┌────▼─────┐            ┌────────▼────────┐         ┌────────▼────────┐
│ Attack   │            │  Agent Runner   │         │  Defense Stack  │
│ Registry │───inject──▶│  (ReAct / tool  │◀────────│  (composable    │
│          │            │   calling loop) │         │   middleware)   │
└──────────┘            └────────┬────────┘         └─────────────────┘
                                 │
                        ┌────────▼────────┐
                        │  Trace Store    │  every tool call, every
                        │  (JSONL/SQLite) │  token, every ms, every $
                        └────────┬────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                  │
        ┌─────▼─────┐    ┌───────▼──────┐   ┌───────▼───────┐
        │ Security  │    │   Utility    │   │  Cost/Latency │
        │  Scorer   │    │    Scorer    │   │    Rollup     │
        └───────────┘    └──────────────┘   └───────────────┘
```

The **defense-as-middleware** interface is the core design decision. Every defense implements the same hooks so they compose:

```python
class Defense(Protocol):
    def on_tool_result(self, result: ToolResult, ctx: Ctx) -> ToolResult: ...   # sanitize/delimit/flag
    def on_pre_tool_call(self, call: ToolCall, ctx: Ctx) -> Decision: ...        # allow / block / escalate
    def on_pre_generate(self, messages: list[Msg], ctx: Ctx) -> list[Msg]: ...   # rewrite context
    def cost(self) -> CostRecord: ...                                            # extra tokens, ms, $
```

Get this interface right in week 1 and everything after is filling in implementations. Get it wrong and composition becomes impossible — which kills claim C3, your most novel result.

---

## 5. Attack families

Use the families already characterized in the literature. Your job is coverage and *adaptivity*, not invention.

| Family | Mechanism | Notes |
| --- | --- | --- |
| Naive instruction | Plain imperative in retrieved content | Weak floor |
| Ignore-previous | Explicit override framing | Classic baseline |
| Important-instructions | Fake system/authority framing | Highest static ASR in AgentDojo |
| Context-completion | Fake conversational turn making the injection look like prior agreed context |  |
| Tool-description poisoning | Malicious instructions in the *tool schema*, not the data ⭐ | Your MCP suite; different trust path entirely |
| Encoding / obfuscation | Base64, homoglyph, zero-width, markdown-comment concealment | Directly targets classifier defenses |
| Multi-turn / delayed | Payload plants state; trigger fires on a later step | Targets stateless per-call filters |

⭐ Tool-description poisoning is your differentiator. It enters through the tool schema, which most defenses implicitly trust, and it's the surface the MCP ecosystem actually exposes.

### Adaptive protocol (this is the methodological contribution)

For each (defense, attack family) pair, run a **black-box, budgeted refinement loop**:

1. Attacker sees only: did the injected task succeed, and did the defense visibly intervene
2. Up to **N=20 refinement rounds**, LLM-driven mutation within the family
3. Fixed compute budget per pair, so numbers are comparable across defenses
4. Report both **ASR@1** (static) and **ASR@20** (adaptive)

The gap between those two columns *is* claim C1. Fixing the budget is what makes the comparison fair — say this explicitly in the writeup, because it's the part a careful reader will probe.

---

## 6. Defenses to implement

Ordered by build effort. ⚡ = the 3-week subset.

| # | Defense | Type | Expected cost |
| --- | --- | --- | --- |
| 1 | **No defense** ⚡ | baseline | — |
| 2 | **Spotlighting / delimiting** ⚡ | prompt-level | ~free; known to vary wildly by model |
| 3 | **Instructional prevention** ⚡ | prompt-level | ~free |
| 4 | **Guard-model classifier** ⚡ | filter | +1 model call per tool result |
| 5 | **Known-answer / canary detection** | filter | +1 call; strong static, weak adaptive |
| 6 | **Tool-call allowlisting + argument policy** ⚡ | runtime | ~free, real utility tax |
| 7 | **Dual-LLM / quarantined LLM** | architectural | large latency + utility cost |
| 8 | **CaMeL-style capability enforcement** | architectural | highest effort, strongest guarantee |

Implement 1–6 first. 7 and 8 are the ones that make the project *impressive* — they're architectural, they're where the interesting utility tax lives, and few people have implemented them outside the original papers. Budget a full week for #8 alone.

---

## 7. Metrics

**Security**

- **ASR@1** (static) and **ASR@20** (adaptive) — attack success = injected task completed
- **Partial compromise rate** — agent took a step toward the injected goal but didn't complete it. Binary ASR hides a lot of near-misses.
- **Detection rate** vs. **false-positive rate** for filter-type defenses, as a full ROC — a guard model at a different threshold is a different product

**Utility** (must be measured on *benign* runs, no injection present)

- **Task completion rate** with the defense on vs. off — this is the utility tax, the number practitioners care most about
- **Over-refusal rate** — benign tasks blocked by the defense
- Report utility on the same task set for every defense, or the comparison is meaningless

**Cost**

- p50/p95 latency per task, broken out by defense overhead vs. base agent
- $/task and extra tokens per task
- For dual-LLM/CaMeL: number of extra model calls per task

**Composition** (claim C3)

For the top 4 defenses, run all pairs. Report observed joint ASR against the independence-assumption prediction (`ASR_A × ASR_B`). Where observed ≫ predicted, the defenses share a blind spot — name it. This table is the most original thing in the project.

---

## 8. Phases

**Week 1 — Substrate + interface** ⚡
Install AgentDojo, reproduce a published number from the paper on one model. *Reproducing someone else's result before adding to it is a credibility move — put it in the README.* Define the `Defense` protocol and trace schema. Wire up cost/latency instrumentation.

**Week 2 — Defenses 1–6 + static baseline** ⚡
Full static sweep: 6 defenses × 5 attack families × 3 models. First results table.

**Week 3 — MCP tool-poisoning suite** ⚡
Build ~15 mock MCP servers with realistic tool schemas; ~40 poisoned-description cases. Run the same defense sweep. Expect the finding that prompt-level defenses underperform here because they guard the data path, not the schema path.

**Week 4 — Adaptive attack loop**
Budgeted refinement. Re-run everything. This is where claim C1 materializes and where your numbers start disagreeing with published rankings.

**Week 5 — Dual-LLM and CaMeL-style defenses**
The architectural tier. Measure the utility tax carefully — this is your headline tradeoff.

**Week 6 — Composition matrix + model sweep**
Pairs of top-4 defenses. Expand to 5–6 models across tiers to test the MCPTox "capability increases vulnerability" finding independently.

**Week 7 — Writeup + release**
Pareto plot, selection guide, blog post, dataset release, reproduction script.

---

## 9. Headline deliverable

The artifact people will actually share — a **defense selection guide**:

| Defense | ASR@1 | ASR@20 | Utility retained | p95 overhead | $/task Δ | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| None |  |  | 100% | — | — |  |
| Spotlighting |  |  |  |  |  |  |
| Instructional prevention |  |  |  |  |  |  |
| Guard model |  |  |  |  |  |  |
| Canary detection |  |  |  |  |  |  |
| Tool allowlist |  |  |  |  |  |  |
| Dual-LLM |  |  |  |  |  |  |
| CaMeL-style |  |  |  |  |  |  |

Plus the **Pareto scatter**: adaptive ASR on x, utility retention on y, bubble size = $/task. Everything below the frontier is a defense nobody should ship. That single chart is your portfolio piece.

---

## 10. Tech stack

| Layer | Pick | Why |
| --- | --- | --- |
| Benchmark substrate | `agentdojo` (PyPI) | Don't rebuild it. Extend it. Its extensibility API is the intended path. |
| Agent loop | AgentDojo's runner, or ~150 lines of your own | Own it if you want to discuss it in depth |
| Defense layer | Your middleware protocol | The actual contribution |
| Trace store | SQLite + JSONL | Every run reproducible; you'll re-analyze constantly |
| Models | 2 frontier + 2 mid + 2 open-weight | Cross-vendor is essential; single-vendor results get dismissed |
| Adaptive loop | LLM-driven mutation, fixed budget | Black-box keeps it cheap and realistic |
| Analysis | pandas + matplotlib | Pareto plot is the deliverable |
| Cost control | Aggressive response caching | You will re-run the sweep a dozen times. Budget $200–400 in API spend and cache from day one. |

---

## 11. Resume bullets

- Built a uniform evaluation harness for 8 prompt-injection defenses on tool-using agents, extending AgentDojo with a novel MCP tool-poisoning suite (40 cases, 15 mock servers)
- Showed that adaptive attacks reorder the published defense ranking: 3 of 8 defenses dropping from <10% to >50% attack success once the attacker adapts within a fixed 20-round budget
- Quantified the security/utility frontier across 6 models — capability-enforcement defenses held attack success under 5% at a 9-point task-completion cost and 2.1× latency
- Measured defense composition, finding stacked defenses underperform independence predictions by up to 3× due to correlated blind spots in schema-path attacks

*(Illustrative numbers — the shape is what matters.)*

---

## 12. Traps

- **Rebuilding AgentDojo.** Your value is the defense/adaptive/cost layer. Reproduce their baseline in week 1 and move on.
- **Measuring security without utility.** A defense that blocks everything scores perfectly and is worthless. Every security number needs its utility twin on the same task set.
- **Unbudgeted adaptive attacks.** If defense A got 5 refinement rounds and defense B got 50, you've measured nothing. Fix the budget, state it prominently.
- **Single-model results.** Injection robustness varies enormously across models — spotlighting halves attack success on some models and does nothing on others. One model tells you nothing generalizable.
- **Uncached API spend.** The full sweep is defense × attack × model × task × refinement rounds. That multiplies fast. Cache aggressively or you'll burn four figures.
- **Framing it as offense.** Same code, wrong README, completely different reception.

---

## 13. Interview questions this prepares you for

- How would you decide what injection defense to ship, given a latency budget?
- Why is static attack success a misleading metric?
- Where does defense-in-depth fail to compose, and why?
- Why are more capable models sometimes *more* vulnerable to injection?
- What's the difference between guarding the data path and the tool-schema path?
- How would you monitor for injection attempts in production, as opposed to blocking them?
- What's the utility cost you'd accept for provable security, and who decides?

The first and last are questions real teams are actively arguing about. Having measured answers is unusual.

---

## Prior art to read before week 1

- AgentDojo — arxiv.org/abs/2406.13352 · github.com/ethz-spylab/agentdojo
- InjecAgent — arxiv.org/abs/2403.02691
- Adaptive Attacks Break Defenses — arxiv.org/abs/2503.00061
- CaMeL / Defeating Prompt Injections by Design — arxiv.org/abs/2503.18813
- MCPTox — arxiv.org/abs/2508.14925
- Lessons from Defending Gemini — arxiv.org/abs/2505.14534