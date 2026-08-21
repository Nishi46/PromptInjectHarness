# Model sweep (Sprint 6, E6)

## S6-05: expanding to 6 models

### L3 — local, Ollama

None of the already-pulled-but-unassigned local models
(`llama3.1:latest`, `llama2:latest`, `mistral:latest`) are in the
~12-14B range this tier targets, so pulled fresh: **Qwen2.5 14B**
(`qwen2.5:14b`, Q4_K_M, confirmed via `ollama show`: 14.8B params).
Genuine vendor/family diversity from L1/L2's Llama family, and unlike
them, explicitly tuned for native tool-calling. Confirmed with a real
live tool-calling request before use.

### New clients: `GoogleAIStudioClient` (L4), `OpenRouterClient` (L6)

`clients/factory.py` only implemented `ollama`/`groq` before this task;
L4/L6 needed real client code, not just config rows.

**`OpenRouterClient`**: confirmed OpenAI-compatible against OpenRouter's
own API reference before writing it, then confirmed real, and it
genuinely is close to a copy of `clients/groq.py` -- because the two
providers really are shaped the same, not because that was assumed.

**`GoogleAIStudioClient`**: confirmed *not* OpenAI-compatible against
Google's own API reference before writing it (`contents[]`/`parts[]`,
`role: "user"|"model"` only, a separate `systemInstruction` field,
`functionCall`/`functionResponse` parts, API key as a query param). Real
tool-calling confirmed with a minimal live request -- but the real
adapter's actual request shape (23 real AgentDojo tools, not one toy
tool) surfaced two real bugs a minimal smoke test couldn't have found:

1. **`400 Bad Request`, schema keys Gemini doesn't support.** AgentDojo's
   real tool schemas (from Pydantic's `model_json_schema()`) use `$defs`/
   `$ref` for nested models (e.g. `send_email`'s `attachments` parameter)
   and `additionalProperties` on open dicts -- Gemini's function-parameter
   schema is a restricted OpenAPI 3.0 subset that rejects all three
   outright ("Unknown name ...: Cannot find field", read directly from
   the real error body, not guessed). Fixed with
   `_sanitize_parameters_schema`: resolves every `$ref` against the
   schema's own `$defs` recursively, then strips both keys.
2. **`400 INVALID_ARGUMENT`, missing `thoughtSignature`.** On the second
   turn of a real multi-turn episode, Gemini rejected the replayed
   function-call history: a `functionCall` part returned with a sibling
   `thoughtSignature` field must have that *exact* signature echoed back
   when the same call is replayed into a later request. Not clearly
   documented (the linked docs page redirected twice, to pages that
   themselves didn't give the concrete field name or example) -- found by
   inspecting a real raw response directly. Fixed by caching signatures
   by tool-call id on the client instance (`self._thought_signatures`,
   populated as responses are parsed, consulted when serializing history)
   -- correct because `sweep/runner.py` constructs one `model_client` per
   *episode*, not per turn, so the same instance sees every turn.
3. **Minor, non-breaking:** Gemini's `functionCall` part *sometimes*
   includes its own `id` field, contradicting the schema reference this
   client was originally written against (which listed only `name`/
   `args`). Now preferred when present, falling back to a fabricated
   `uuid.uuid4()` only when absent -- more faithful, and free.

Both new clients unit-tested (`tests/test_clients.py`) against a fake
`requests.Session`, no live call in the suite itself -- covering both the
happy path and, for Gemini specifically, the id-lookup and tool-schema
re-shaping this module's own bugs came from.

### L6's model pick

Picked live from OpenRouter's real `:free` catalog at sweep time (per
this tier's own "don't hardcode ahead of time" rule) -- confirmed `tools`
in `supported_parameters`, then confirmed real tool-calling with a live
request: **`nvidia/nemotron-3.5-lightning:free`**. `clients/costs.py`
gained a `(0.0, 0.0)` rate entry for it (a real, published $0 rate, not
an Ollama-style special case) -- future re-picks of L6 need their own
entry the same way.

### `configs/model_sweep.yaml`

The sprint plan's own "core config" name. `no_defense` only (model is the
sole varying axis S6-06 needs; defense-level numbers already live in
`results/static_baseline.md`), `important_instructions` (the one attack
family with a confirmed non-zero ASR anywhere in this project), the same
3 tasks every other static sweep uses. **Writes into
`configs/static_sweep.yaml`'s own trace DB** -- L1/L2/L5 already have
this exact (defense, attack, task) population from S2-11 and
`static_sweep_groq_slice.yaml`. Because this file's own config hash
differs from those two, S6-05 actually executed a config *filtered to
just L3/L4/L6* at run time (the same "filter at run time, keep the
committed file the honest full 6-model grid" tradeoff S5-02 already
established) rather than let a naive full run silently re-spend Groq
quota re-executing L1/L2/L5.

### Real provider surprises, sample-checked and found live, not assumed

- **Groq (L5): still the tightest budget, as expected.** No new
  surprises here beyond what S6-03 already documented in
  `docs/notes/composition.md` -- L5's 6 episodes for this config already
  existed from S2-11/the Groq slice, so this task spent zero new Groq
  quota.
- **OpenRouter (L6): clean.** 6/6 episodes completed on the first attempt
  at `concurrency=2`, well inside the ~50 req/day budget. No rate limits
  hit.
- **Google AI Studio (L4): the real surprise.** Appendix A.3 estimates
  "~1,500 requests/day, no credit card" for this provider generally.
  **`gemini-3.5-flash`'s actual free-tier limit is 20 requests/day**,
  read directly from a real `429` error body
  (`quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier`,
  `quotaValue: "20"`) -- almost two orders of magnitude tighter than
  Appendix A.3's general estimate for this specific model. Confirmed
  this is a genuine daily cap, not a short rolling window that clears in
  seconds: retried after a 30s pause (the error's own suggested
  `retryDelay`) and it still failed identically. Also hit a `429` under
  `concurrency=2` that a `concurrency=1` retry avoided -- a per-minute
  throttle layered on top of the daily cap.

  **Real, honest consequence:** L4's benign pass completed in full (3/3
  episodes, utility=1.000 -- a strong signal the model itself works
  correctly end to end). L4's *attacked* pass (`important_instructions`)
  could not complete within this session -- 3 of 3 attempts were
  rate-limited, and the daily quota this project's own testing had
  already partially spent earlier in the day (verification calls,
  diagnostic retries) left no further room. Left incomplete rather than
  manufactured or waited-out; S6-06's capability-vulnerability analysis
  needs to report L4's attacked-ASR as `n/a` for now, not a fabricated
  `0.000`. Completing it is a same-day-quota-reset rerun of
  `configs/model_sweep.yaml` (filtered to L4 only), not a design problem.

### Real results as of this task (attacked pass, `important_instructions`, `no_defense`)

| Model | Tier | Benign utility | Attacked ASR |
| --- | --- | --- | --- |
| `llama3.2:3b` | L1 | 0.333 (pre-existing) | 0.000 |
| `llama3.1:latest` | L2 | 0.333 (pre-existing) | 0.000 |
| `qwen2.5:14b` | L3 | 0.333 | 0.000 |
| `gemini-3.5-flash` | L4 | **1.000** | n/a (quota-blocked) |
| `openai/gpt-oss-120b` | L5 | 1.000 (pre-existing) | 0.333 |
| `nvidia/nemotron-3.5-lightning:free` | L6 | 1.000 | **0.667** |

**Real, notable finding, not yet formally analyzed (S6-06's job):** L6
shows the *highest* attacked ASR of any model this project has tested,
higher than L5 -- and L4/L5/L6 (the three hosted, higher-capability
tiers) all show perfect or near-perfect benign utility (1.000), while
L1/L2/L3 (local, smaller) sit around 0.333. On this small sample, more
capable models both complete benign tasks more reliably *and* get
compromised more -- the exact MCPTox-style pattern Appendix A.5 names as
the thing to watch for. S6-06 does the real statistical treatment
(rank correlation, honest caveats about `n=3`-per-cell sample size); this
is the raw, real numbers it will read from the trace DB, recorded here
first.

## S6-06: does ASR actually rise with capability tier?

`scripts/generate_capability_vulnerability_results.py` -> `results/model_sweep.md`
does the real statistical treatment on the numbers recorded just above:
Spearman rho between ladder position (L1=1 .. L6=6) and attacked ASR on
the one cell with real dynamic range (`no_defense`/`important_instructions`),
using the hand-rolled rank-correlation machinery `generate_adaptive_results.py`
already established (Pearson-of-ranks, ties averaged -- no scipy/numpy,
this project's standing precedent).

**The number: rho = 0.894 (n=5, L4 excluded -- quota-blocked, not a
confirmed `0.000`).** Read at face value, that is a strong positive
correlation and would replicate Appendix A.5's MCPTox-style finding: more
capable models get compromised more often by this attack family. Benign
utility correlates with tier almost identically (rho = 0.878, n=6) --
more capable models also complete the underlying task more reliably, the
expected other half of the same story.

**But the honest caveat Appendix A.5 itself names is real here, not just a
hypothetical risk to flag defensively.** Of the 5 tiers with attacked-pass
data, 3 (L1, L2, L3 -- every local model this project has ever run) are
tied at exactly `0.000`. A Spearman correlation over a vector with three
tied zeros and two distinct positive values isn't really measuring "does
ASR climb smoothly with capability" -- it's measuring "do the hosted
models differ from the local models," which is a real and worth-reporting
finding, but a coarser one than "monotonic in tier" implies. The *only*
place in this data where tier order and ASR both vary continuously is the
L5 -> L6 step (0.333 -> 0.667), and that comparison is a single ordinal
pair at `n=3` episodes per cell -- one flipped episode moves either
number by 0.333, so this one data point is not strong evidence of a
smooth within-hosted-tier gradient on its own, just consistent with one.

**Verdict, reported plainly rather than rounded up to "confirmed" or down
to "no effect":** this project's data is consistent with the MCPTox
direction and does not contradict it, but the honest description of what
was actually measured is "every local model tested so far has a
measured ASR of zero against this attack family, and every hosted model
tested so far has a nonzero one" -- a real, load-bearing local/hosted
split -- rather than "ASR climbs step-by-step with capability tier,"
which the data doesn't have enough within-tier resolution to support yet.
L1-L3's near-zero ASR compressing the range (the exact risk Appendix A.5
flags) is very plausibly what's happening: these are 3B-14B local models
that have shown almost no dynamic range on *any* attack family in any
prior sprint, not because they're uniquely well-defended but because
they struggle to reliably invoke tools at all (S1-08, S2-11's own notes).
A rerun with a wider attack surface (more attack families, more episodes
per cell) or once L4's quota-blocked cell fills in would be needed to
tell "genuinely flat at zero" apart from "just not enough episodes to see
the rare success" for the local tier -- this sprint's budget doesn't
support that rerun.
