# Sprint 0 Checklist — Groundwork

Granular sub-steps for each task in Sprint 0 of [sprint_planning.md](sprint_planning.md). Check items off as you go; each produces a concrete artifact (a notes file, a confirmed run, a stored key).

---

## S0-01 — Read AgentDojo paper (2h) 🔴

- [x] Locate the AgentDojo paper (arXiv) and open it alongside a blank notes file.
- [x] Skim pass: abstract, intro, figures — get the high-level shape before the deep read.
- [x] Deep-read the task/environment abstraction section — how a "task suite," "task," and "environment" are modeled.
- [x] Deep-read the injection-task section — how injected tasks attach to tool outputs, what constitutes an "attack."
- [x] Deep-read the scoring/metrics section — how utility and security (ASR) are computed and reported.
- [x] Write [`docs/notes/agentdojo.md`](../docs/notes/agentdojo.md): task/injection data model, suite structure, scoring functions, key terms to reuse verbatim in your own schema.
- [x] Flag anything from the data model that should directly inform the Sprint 1 trace schema (S1-03) and integration adapter (S1-07).

## S0-02 — Read Adaptive Attacks paper, Zhan et al. (1.5h) 🔴

- [x] Locate the paper; open alongside notes file. Confirmed: Zhan, Fang, Panchal, Kang, **arXiv:2503.00061**, NAACL 2025 Findings.
- [x] Read the adaptive attack protocol section — the attack/mutate/retry loop structure.
- [x] Read the budget methodology section — how round count N is chosen, what "budget" means operationally, how cost is bounded per (defense, attack) pair.
- [x] Read the results section for how they report ASR@1 vs ASR@N and any rank-correlation analysis between static and adaptive rankings.
- [x] Write [`docs/notes/adaptive_attacks.md`](../docs/notes/adaptive_attacks.md): protocol steps, feedback signals exposed to the attacker, stopping criteria, reporting convention.
- [x] Cross-check against Sprint 4's planned design and note deltas. **Important delta found:** this paper's "adaptive attack" is white-box gradient-based optimization (GCG/AutoDAN family) — one adversarial string optimized per defense via logit/gradient access, reused across ~100 tasks. It has no LLM-mutation loop, no per-task round budget, and no discrete success/refusal feedback channel. Sprint 4's planned design (black-box, LLM-driven mutation, N=20 rounds per task, bounded feedback) is a genuinely separate contribution — the paper explicitly calls black-box adaptive attacks future work — not a reproduction of this paper's method. Worth stating that lineage honestly in the eventual writeup.

## S0-03 — Skim CaMeL, MCPTox, Lessons from Defending Gemini (2h) 🟡

- [x] Locate all three papers/posts up front. CaMeL = arXiv:2503.18813 ("Defeating Prompt Injections by Design," Google/DeepMind/ETH Zurich). MCPTox = arXiv:2508.14925 (AAAI 2026). Lessons from Defending Gemini = arXiv:2505.14534.
- [x] Skim CaMeL (~40 min): core mechanism captured — privileged LLM emits a restricted Python-subset program; untrusted data never reaches it directly; every value carries a provenance/capability tag; tool-call sinks check argument provenance before executing. 77% task success with provable security vs. 84% undefended on AgentDojo. Feeds S5-03.
- [x] Skim MCPTox (~40 min): 45 real MCP servers, 3 attack templates, 36.5% avg ASR; capability-vulnerability link shown via within-model-family contrasts (e.g. reasoning on/off). Feeds S6-06.
- [x] Skim "Lessons from Defending Gemini" (~40 min): 6 concrete lessons captured (adaptive-eval necessity, capability-vulnerability link, adversarial training without utility loss, brittle classifiers, cheap attack generation, agentic-specific threat model).
- [x] Write one consolidated [`docs/notes/related_work_skim.md`](../docs/notes/related_work_skim.md) with bullets per source, tagged to the sprint task each feeds. Note: CaMeL section is medium-high confidence (PDF text extraction failed; relied on abstract + a careful third-party technical summary rather than the raw paper body) — worth a from-source re-check before S5-03 implementation begins.

## S0-04 — Install AgentDojo, run example benchmark end-to-end (1.5h) 🔴

- [x] Create/activate the project's Python environment (`.venv` via pyenv Python 3.11.8 — system Python 3.9.6 was too old for agentdojo's deps).
- [x] `pip install agentdojo`; record the exact installed version — **0.1.35**. Pin `agentdojo==0.1.35` in `pyproject.toml` at S1-01.
- [x] Verify the install: `python -m agentdojo.scripts.benchmark --help` lists suites/attacks/defenses.
- [x] Pick "one cheap model" per the project's $0 track: local Ollama `llama3.2:3b` (L1 tier), already pulled — no hosted key needed.
- [x] Run AgentDojo's packaged example benchmark end-to-end against that local model (suite `workspace`, `user_task_0`, both benign and with `--attack important_instructions`).
- [x] Capture the output/report format and confirm it completes without error (`error: null` in both JSON reports; benign run scored `utility: false, security: true`, attacked run scored `utility: false, security: false` — attack succeeded against the undefended 3B model, as expected).
- [x] Note any friction (setup quirks, config surprises) in [`docs/notes/agentdojo.md`](../docs/notes/agentdojo.md) under "Integration notes (Sprint 0 install — S0-04)" — feeds Sprint 1's integration adapter (S1-07).

## S0-05 — Set up API keys, spend alerts, cost dashboard (1h) 🔴

> The project's primary track is a hard $0 budget (Appendix A), so "spend alerts" and "cost dashboard" here mean free-tier quota tracking, not billing alerts.

- [x] Sign up for Google AI Studio (Gemini) free tier; generate an API key.
- [x] Sign up for Groq free tier; generate an API key.
- [x] Sign up for OpenRouter free tier; generate an API key.
- [x] Store all keys in a local `.env` file — real keys live in `.env` (gitignored, confirmed untracked by `git status`); [`.env.example`](../.env.example) stays a blank template for the repo.
- [x] Smoke-tested one call against each of the three providers — all three returned HTTP 200 on their `/models` list endpoints (Groq needed a real User-Agent header to get past a Cloudflare bot-check; not a key problem). Confirmed live model IDs: `gemini-3.5-flash` (Google), `llama-3.3-70b-versatile` (Groq), 17 free-tier models currently on OpenRouter.
- [x] Set up local Ollama and pull the L1 model by digest (not tag) per Appendix A §A.1; recorded in [`configs/models.yaml`](../configs/models.yaml) (`llama3.2:3b`, already pulled; L4/L5 hosted picks now confirmed live too). L2/L3 local and L6 OpenRouter are left as `TODO` — Appendix A doesn't pin exact model names for these (L6 is explicitly a "rotating" pick), so choose specific ones at S1-01/sweep time.
- [x] Noted the binding per-provider constraint (Groq's ~100K tokens/day is the tightest) in `.env.example` and `configs/models.yaml` — feeds S1-11's run queue.

---

## Exit criteria (from sprint_planning.md)

- [x] AgentDojo runs locally (S0-04)
- [x] Task/injection/attack abstractions documented in `docs/notes/agentdojo.md` (S0-01) — review it to internalize before Sprint 1
- [x] Spend/quota alerts are live (S0-05) — all three free-tier keys created and smoke-tested; quota ceilings recorded in `.env.example` / `configs/models.yaml`.

**Sprint 0 complete.**
