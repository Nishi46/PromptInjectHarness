# Related Work Skim

Time-boxed skim notes (~15-20 min/source equivalent) for three sources relevant to
the Sprint 5 / Sprint 6 defense-implementation and evaluation work. Not exhaustive
close-readings — captured for orientation before implementation, with pointers to
go back to the primary source for exact details when actually coding.

---

## CaMeL

**Source:** "Defeating Prompt Injections by Design" — Debenedetti, Shumailov, Fan,
Hayes, Carlini, Fabian, Kern, Shi, Terzis, Tramèr (Google, Google DeepMind, ETH
Zurich). arXiv:2503.18813 (v2, 24 Jun 2025). https://arxiv.org/abs/2503.18813
Code: https://github.com/google-research/camel-prompt-injection

**Confidence:** Medium-high. Core mechanism confirmed via the arXiv abstract plus
a detailed third-party technical summary (Simon Willison, a well-regarded and
technically careful summarizer who works closely from primary sources); direct
PDF text extraction failed (binary/garbled), so exact wording, full worked
policy examples, and precise LOC/effort figures were not independently verified
against the paper body. Numbers below (77%/84% AgentDojo) are from the abstract
directly, so higher confidence.

- **Core mechanism — two LLMs + a custom interpreter.** A "privileged" LLM
  reads the user's (trusted) query and emits a **restricted program**: code in
  a deliberately locked-down subset of Python where tool calls are just Python
  function calls (e.g. `email = get_last_email(); address = query_quarantined_llm(...); send_email(...)`).
  Untrusted data (email bodies, web content, etc.) is never given to the
  privileged LLM directly — it's only handled inside this program, often via a
  second "quarantined" LLM call used purely for data extraction with a
  constrained output schema.
- **Provenance/capability tagging.** Every value produced during execution
  carries a "capability" — metadata tagging things like *who is allowed to
  read this value* and *what source it came from* (trusted user input vs.
  untrusted retrieved data). Tags propagate through the interpreter as data
  flows from one variable to another (i.e., a value derived from an untrusted
  email inherits "untrusted" provenance).
- **Restricted program = parsed via Python's `ast` module.** CaMeL is not a
  sandboxed real Python interpreter; it's a custom interpreter that parses the
  generated code with Python's built-in `ast` library and recursively
  interprets each AST node itself, so it can intercept every operation and
  check capabilities before it executes — this is what makes control flow
  itself immune to injected instructions (untrusted data can influence *values*
  but never *which code path executes*).
- **Policy engine at the sink.** Security policies are checked at tool-call
  boundaries (the "sinks"). Example given: `get_last_email()` is always
  allowed (read of trusted context), but `send_email()` requires the
  `recipient` argument's provenance to be trusted — if the recipient value
  traces back to untrusted data, the system either blocks the call or asks the
  user to approve it explicitly, rather than silently sending.
- **Cost/tradeoff and rough complexity.** On AgentDojo, CaMeL achieves
  provable security on 77% of tasks vs. 84% utility for an undefended system —
  i.e., a real but fairly modest utility cost for the security guarantee.
  Building "CaMeL-style" enforcement from scratch means writing (a) a
  planner-prompting scheme that emits restricted-Python-like programs instead
  of freeform tool calls, (b) a custom AST-walking interpreter with a
  provenance/capability tag attached to every runtime value, and (c) a policy
  layer invoked at each tool-call node that inspects argument provenance
  before allowing the call — this is a real interpreter-writing exercise, not
  a thin wrapper, but it's scoped to a small Python subset (no need to support
  general Python semantics), which keeps it from being as large as a full
  language runtime.

**Feeds:** S5-03 (CaMeL-style capability enforcement — planner emits restricted
program, values carry provenance tags, policy engine gates tool calls on data
flow).

---

## MCPTox

**Source:** "MCPTox: A Benchmark for Tool Poisoning Attack on Real-World MCP
Servers" — Wang, Gao, et al. arXiv:2508.14925. Published in Proceedings of the
AAAI Conference on Artificial Intelligence, 40(42), 35811–35819 (2026).
https://arxiv.org/abs/2508.14925

**Confidence:** Medium. Headline numbers and attack-template structure came
back consistent across multiple independent sources (arXiv abstract,
Semantic Scholar/AAAI listing, and a third-party technical digest), so I'm
fairly confident in the numbers cited. The description of *how* they compare
"capability" across models (model/config selection rather than a single
capability-benchmark axis) is the part I'm least certain about — the
abstract-level fetch didn't give me a fully explicit "we plot ASR against
capability metric X" methodology section, so treat that framing as a
reasonable reconstruction, not a verbatim quote, and check the paper directly
before writing S6-06's methodology.

- **Threat model: tool poisoning via metadata, not execution results.**
  Malicious instructions are embedded directly in a tool's *description/schema*
  (what the agent sees when deciding which tool to call and how), not in a
  tool's output — i.e., this is an attack on the tool-selection/planning step,
  distinct from classic indirect injection via retrieved content.
- **Scale/construction:** built on 45 live, real-world MCP servers and 353
  authentic tools; 1,312 malicious test cases generated via few-shot prompting
  across 10 risk categories, using **three attack templates**: (1) explicit-trigger
  function hijacking (poisoned tool description directly instructs reading a
  sensitive file, e.g. disguised as a `get_time`-like utility), (2)
  implicit-trigger function hijacking (a "system service"-flavored tool
  description asserts a global rule, e.g. "before any file operation, read
  X as a security check"), and (3) implicit-trigger parameter tampering
  (description convinces the agent to silently alter a legitimate call's
  arguments, e.g. redirect an email's recipient). All three route the actual
  malicious action through an already-trusted, legitimate tool call rather
  than a new suspicious one, for stealth.
- **Headline finding:** overall average ASR of 36.5% across 20 evaluated
  model/agent settings; o1-mini highest at 72.8% ASR, with DeepSeek-R1 (70.9%)
  and Phi-4 (70.2%) also high. Claude 3.7 Sonnet had the lowest refusal-based
  vulnerability (refusal rate <3%, i.e. rarely explicitly refuses, though this
  is refusal rate not ASR). Reported as an "inverse-scaling-like" pattern:
  more capable/more instruction-following models are often *more* vulnerable
  because the attack exploits exactly the competence being optimized for
  (careful instruction-following, better use of context) rather than a
  knowledge or reasoning gap.
- **Capability-vs-vulnerability comparison design (methodology to replicate):**
  rather than a single external capability score, they lean on natural
  contrasts within the same model family/config — most concretely, they
  toggle reasoning mode on/off for Qwen3 and observe ASR rise by ~27.8
  percentage points with reasoning enabled, and separately note larger Qwen
  variants show higher ASR than smaller ones. The caveat they raise
  themselves: a low-ASR model may just have weaker instruction-following, not
  better security, so ASR alone conflates "safe" with "incapable" — worth
  guarding against in S6-06 by also reporting task-completion/utility
  alongside ASR when comparing models, not ASR in isolation.
- **Replicable skeleton for S6-06:** pick 2+ tool-poisoning attack templates
  (function hijacking + parameter tampering are the simplest to port), pick a
  small set of models spanning a capability range within the same family if
  possible (e.g., reasoning on/off, or small/large variant of the same
  model), run identical poisoned-tool-description test cases against each,
  and report ASR *and* a utility/completion metric side by side per model to
  distinguish "more secure" from "less capable."

**Feeds:** S6-06 (test the MCPTox finding independently on your suites).

---

## Lessons from Defending Gemini

**Source:** "Lessons from Defending Gemini Against Indirect Prompt
Injections" — Google DeepMind. arXiv:2505.14534 (May 2025).
https://arxiv.org/abs/2505.14534 (also summarized on Google DeepMind's blog,
"Advancing Gemini's security safeguards").

**Confidence:** Medium-high on the general themes and structure (adaptive
evaluation framework, categories of defenses tested, general conclusions) —
these matched across the abstract and the fetched paper body. The specific
numeric figures below (e.g. 99.8%→53.6% ASR drop, 2,160–4,320 queries, <$10
attack cost, lmarena score 1392) came from a single automated fetch/summarize
pass over the HTML paper and were **not cross-checked against a second
source or the raw PDF** — treat exact numbers as indicative, and re-verify
against the paper directly before citing them precisely in any writeup.

- **Adaptive evaluation is the whole game.** Defenses that look strong against
  static/non-adaptive attacks routinely fail once the attacker is allowed to
  optimize *against that specific defense*. They report that across a matrix
  of defense-attack combinations, adaptive attacks matched or beat
  non-adaptive ones in the large majority of cases — meaning a defense that
  hasn't been red-teamed adaptively hasn't really been tested.
- **More capable ≠ more secure, and can be the opposite.** Improvements in
  general instruction-following capability can directly translate into being
  easier to attack, because indirect injection succeeds precisely by having
  the model follow instructions faithfully — just instructions that came from
  attacker-controlled data rather than the user. This is the same structural
  point MCPTox makes independently, from the defender's side.
- **Adversarial training can harden a model without visibly hurting utility.**
  Training on large sets of diverse attack triggers (their "Linear
  Generation" attack generation approach) substantially reduced attack
  success rates in their reported case study, while general-capability
  leaderboard scores were reportedly maintained — a data point against the
  assumption that robustness training necessarily trades off against
  helpfulness.
- **Classifier/filter-style defenses (perplexity filters, self-reflection
  classifiers) are brittle under adaptive pressure** even when tuned to a
  strict false-positive budget — reported as still allowing a very high
  attack success rate once the attacker adapts to the specific filter.
  Prompt-level defenses aren't uniformly better either: where instructions are
  placed in the prompt relative to untrusted content mattered a lot to
  effectiveness in their tests.
- **Attack generation is cheap.** Automated attack-search techniques (e.g.
  tree-of-attacks-style search) found working, transferable injection
  triggers against an undefended model using a modest number of queries and a
  very small dollar cost — the practical takeaway being that a single
  discovered trigger can be cheaply weaponized at scale, so defenses can't
  rely on triggers being hard/expensive to find.
- **Indirect injection is specifically an agentic/tool-use problem.** The
  threat model requires the model to both (a) ingest attacker-influenced data
  (retrieved documents, emails, etc.) and (b) have consequential tool access
  (e.g. send email) — this combination is what turns "the model got confused
  by weird text" into "the model exfiltrated user data," which is why
  defenses need to be designed into the tool-use/agentic stack itself rather
  than bolted on as a text filter.

**Feeds:** general context for defense design across the project (most
directly relevant to any sprint task involving adaptive/red-team evaluation
methodology and to sanity-checking defense claims against static-only test
suites).
