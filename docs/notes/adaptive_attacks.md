# Adaptive Attacks Break Defenses Against IPI on LLM Agents — Paper Notes

Source: Qiusi Zhan, Richard Fang, Henil Shalin Panchal, Daniel Kang (UIUC + Nirma
University). **"Adaptive Attacks Break Defenses Against Indirect Prompt Injection Attacks
on LLM Agents."** arXiv:2503.00061 (v2, 4 Mar 2025). Findings of ACL: NAACL 2025. Code:
https://github.com/uiuc-kang-lab/AdaptiveAttackAgent

Confidence: **high**. Read the full PDF text (all 17 pages incl. appendices, prompts, and
hyperparameter tables), not just the abstract. All sections below are sourced directly
from the paper. One related paper is cited for context in the Sprint 4 section but was
only skimmed via abstract (flagged explicitly where used).

## Adaptive attack protocol

This paper's "adaptive attack" is **not** a query/feedback/mutate loop against a live
agent. It is white-box, gradient-based adversarial-string optimization, directly ported
from LLM jailbreak literature (GCG family). Mechanically:

- Attacker can only manipulate the external content `E` that the agent reads back from a
  tool (an email, review, etc.). The malicious instruction `Ia` is embedded in `E`,
  optionally wrapped with an adversarial string `S`: `E = Ia ⊕ S` (suffix) or `S ⊕ Ia`
  (prefix).
- **GCG** (base method): optimizes `S` via coordinate-gradient discrete-token search to
  maximize `P(target | prompt)`, where `target` is a fixed string that would trigger tool
  execution (e.g. `"Thought: I will use the <Ta> tool to"` for the ReAct/ Vicuna-7B agent,
  `'{"name": "<Ta>"'` for the fine-tuned Llama3-8B agent).
- **Multi-objective GCG (M-GCG)**: adds a second loss term so the string also fools a
  detection-based defense (jointly optimized if agent/detector share a tokenizer,
  alternated step-by-step otherwise; weight α = 0.5).
- **Two-stage GCG (T-GCG)**: trains two adversarial strings — one that gets the target
  behavior, one that survives the paraphrasing defense (i.e., the paraphraser turns
  `S1⊕Ia⊕S2` back into something close to `S1⊕Ia`).
- **AutoDAN**: genetic/left-to-right token selection producing semantically coherent
  (lower-perplexity) strings, used specifically to beat perplexity filtering (token-level
  GCG gibberish gets flagged by perplexity; AutoDAN strings don't).
- One adversarial string is trained **per defense** (not per test case), then applied
  unmodified across the whole evaluation subset. There is no per-task mutation step and
  no "try, observe, decide next move" decision procedure — the entire adaptive procedure
  is a single continuous optimization run per (defense, attack-method) pair.

## Budget methodology

- "Budget" = **gradient-optimization steps**, not attack attempts. Table 5 (hyperparams):
  500 steps for most GCG variants, 1000 for AutoDAN and T-GCG stage 1, up to 2000 for
  T-GCG stage 2 (Llama3-8B), with an early-stop rule (halt after 100 consecutive steps
  with no loss reduction).
- Budget is **not uniform** across defenses/attacks — different defenses get different
  step counts and different adversarial-string token lengths (5–150 tokens) depending on
  what the paper's authors found necessary to converge (Appendix A, Table 5). No formal
  justification for the specific numbers is given beyond "we did early stopping and this
  is what worked" — there's no ablation curve of ASR vs. step count.
- Budget is **per (defense × adaptive-attack-method) pair, not per task**. A single
  string is optimized once and then reused against the entire 100-case evaluation subset
  (50 direct-harm + 50 data-stealing cases sampled from InjecAgent's 1,054). This is a
  fundamentally different unit of "budget" than a per-task round cap.
- Compute cost is bounded pragmatically: "~30 minutes on a single A100 GPU" per
  adversarial string — i.e., cost is bounded by wall-clock per optimization run, not by a
  declared query/round budget.

## Feedback signals

This is the most important divergence to flag. The attacker's feedback is **continuous,
white-box gradient/logit access** — not any of the discrete signals a black-box adaptive
loop would use:

- The optimization objective is the log-probability the model assigns to a fixed target
  string, differentiated all the way through the model's own logits (this is exactly what
  GCG needs — it computes gradients w.r.t. one-hot token vectors to guide the greedy
  coordinate search).
- For multi-objective GCG, the "stealth" feedback is the detector's classification
  probability (again gradient-accessible, or alternated-step optimization when
  tokenizers differ) — not a boolean "detected/not detected" flag.
- The paper's own Limitations section states this explicitly: *"Our attacks assume the
  attacker has white-box access to the agent model, defense models, and detailed
  prompts... studying black-box and grey-box attacks is also crucial for a more
  comprehensive evaluation."* Black-box, discrete-feedback adaptive attacks are called
  out as **future work the paper does not do.**
- There is no notion of "agent's visible refusal text" as feedback — the whole approach
  operates at the token/logit level against a fixed training target, independent of
  whatever natural-language refusal the model might produce.

## Results reporting convention

- No ASR@1-vs-ASR@N curve. They report a **two-point comparison**: `ASR-defense` (ASR
  after the defense is deployed, before any adaptive attack) vs. `ASR-adaptive attack`
  (ASR after the fully-optimized adversarial string is applied), plus the pre-defense
  `Original ASR` as a third reference point (Figure 2). Table 4 adds ± std dev by
  breaking `ASR-defense`/`ASR-adaptive attack` out per attack type/stage (direct harm;
  data-stealing step 1/2) and averaging over attack methods — the std dev is variance
  across attack methods, not across "rounds."
- A secondary diagnostic metric, **target rate** (fraction of outputs starting with the
  literal training-target string), is reported to sanity-check optimization quality
  separately from ASR (Figure 3) — useful because target-rate and ASR can diverge (e.g.
  AutoDAN has the lowest target rate but still a high ASR because many outputs are
  semantically-but-not-literally on target).
- **Cross-evaluation / reordering-adjacent analysis** (Figure 5, §6.4): a full matrix of
  ASR for "adversarial string trained against defense X" evaluated against every other
  defense Y. Finding: attacks specialize — the string trained for a given defense is
  usually most effective against that exact defense, and cross-defense transfer is
  weaker. This is the paper's closest analogue to a static-vs-adaptive reordering
  argument, but it is a **qualitative heatmap observation**, not a formal rank-correlation
  statistic (no Spearman/Kendall-tau number is computed anywhere in the paper).
- Headline result: adaptive-attack ASR exceeds 50% against **all eight** defenses, and
  exceeds the no-defense original ASR in most cases — the paper's core claim is "every
  tested defense is broken by an adaptive attacker," which is the qualitative story, not
  a ranking-reordering statistic per se.

## Notes for this project's Sprint 4

`documentation/sprint_planning.md` (S0-02, lines 140–159) already names this paper as the
required reading for Sprint 4 and largely borrows its vocabulary (ASR@1/ASR@N framing,
"reordering" claim). Confirmed correct paper/authors/arXiv ID — no correction needed
there. But the actual mechanics diverge from the plan in several load-bearing ways worth
knowing before work starts:

1. **S4-01 (feedback channel: success bool, defense-intervened bool, refusal text) has no
   precedent in this paper.** Zhan et al.'s attacker gets full white-box gradient/logit
   access — categorically more information than any of the three signals S4-01 proposes.
   The paper explicitly flags black-box/grey-box adaptive attacks as future work it
   doesn't attempt. This project's Sprint 4 is *filling that stated gap*, not
   reproducing the paper's threat model — worth saying so explicitly in the eventual
   writeup, since it's a genuine point of methodological distinction, not a
   simplification. (A different, newer paper — Nasr, Carlini, Tramèr et al., "The
   Attacker Moves Second," arXiv:2510.09023, 2025 — sounds closer in spirit to a
   discrete/round-based adaptive framing across a broader defense set including prompt
   injection; I only read its abstract, not full text, so treat this as a lead to check
   before Sprint 4, not a confirmed methodological match.)

2. **S4-02 (LLM-driven mutation engine) has no precedent either.** The paper's "mutation"
   is discrete coordinate-gradient token substitution (GCG) or a genetic/left-to-right
   token search (AutoDAN) — not an LLM rewriting its own payload. If Sprint 4's writeup
   cites this paper as the direct inspiration for the mutation *mechanism*, that
   overstates the connection; it's inspiration for the *framing* (adaptive > static,
   attacker adapts post-disclosure) not the *implementation*.

3. **S4-03 (hard cap N=20 rounds per (defense, attack, task), identical budget for every
   pair) is a stricter and differently-shaped budget than the paper's.** Zhan et al.
   budget per (defense × attack-method) — reusing one string across ~100 tasks — and
   budgets are *not* identical across defenses (500–2000 optimization steps depending on
   defense, Table 5). There is no per-task adaptation in the paper at all. Sprint 4's
   design (per-task rounds, uniform budget) is more granular and more principled about
   fairness than what this paper actually did — that's a genuine improvement, not
   something to second-guess, but don't expect the paper to justify why N=20 specifically
   is the right number; it doesn't run any budget ablation to draw on.

4. **S4-07 (ASR@1 vs ASR@20, rank-correlation between static/adaptive orderings) is
   original relative to this paper.** The paper reports only a two-point ASR-before/ASR-
   after comparison and a qualitative cross-evaluation heatmap — no incremental ASR@k
   curve, no computed rank-correlation statistic. If Sprint 4 wants a literature
   precedent for the rank-correlation analysis specifically, this paper isn't it; treat
   S4-07 as a genuine methodological contribution of this project rather than a
   reproduction.

5. **Useful result to cite as motivation regardless:** adaptive attacks broke all 8
   defenses tested (detection-based, input-level, model-level) with ASR > 50%, and for
   several defenses (fine-tuned detector, sandwich prevention, adversarial finetuning —
   the paper's "top 3 strongest" under static evaluation) the adaptive attack
   specifically targeting that defense outperformed all other adaptive attacks against
   it (Figure 5) — i.e., defense-specific adaptation matters, which supports the
   S4-02 "constrained to stay within its attack family" design choice even though the
   mechanism differs.

6. **A limitation worth inheriting/citing:** the paper's own adversarial strings only
   reliably control the agent's *first* tool-call action; ASR gains are much smaller for
   the second step of their two-step data-stealing attacks (Table 4, §6.3). If Sprint 4's
   task suite includes multi-step attacker goals, expect a similar first-step bias unless
   the feedback/mutation design explicitly accounts for downstream steps.
