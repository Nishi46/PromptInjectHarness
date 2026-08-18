# Reproduction (S1-08)

**Goal:** prove the harness (S1-07's AgentDojo adapter) computes the same thing AgentDojo's own reference implementation computes, and document any gap honestly.

## Target

| | |
| --- | --- |
| Model | `llama3.2:3b` (Ollama, tier L1, digest `sha256:a80c4f17acd5`) |
| Suite | `workspace` |
| Tasks | `user_task_0, user_task_1, user_task_3, user_task_2, user_task_5, user_task_6` (first 6 by suite order) |
| Attack | `important_instructions`, `injection_task_0` |
| Defense | `no_defense` |
| Benchmark version | `v1.2.2` (AgentDojo's own CLI default) |

**Command:** `.venv/bin/python scripts/reproduce_s1_08.py`

## Why not compare directly against the AgentDojo paper's table

The published AgentDojo numbers are for the models they evaluated (GPT-4, Claude, Gemini, a few large open-weight models) — `llama3.2:3b` isn't one of them, and this project's $0 track (`sprint_planning.md` Appendix A) runs exclusively on free-tier/local models the paper never measured. Comparing our number to theirs for a *different model* wouldn't validate anything about the adapter — it would just confirm a 3B and a frontier model perform differently, which isn't news.

The reproduction that actually matters here — the one that tests whether the *adapter* is correct — is **our harness vs. AgentDojo's own reference code, on the identical model, identical tasks, identical attack.** `scripts/reproduce_s1_08.py` runs both:

1. **Ours**: `injection_pareto.adapters.run_episode` (S1-07) — our `ModelClient`/`DefenseStack` bridged into AgentDojo's pipeline, writing a full trace to `runs/local/reproduction/trace.db`.
2. **Theirs**: `TaskSuite.run_task_with_pipeline` called directly with a pipeline built by AgentDojo's own `AgentPipeline.from_config(provider="local")` — the same code path their `agentdojo.scripts.benchmark` CLI drives, called in-process instead of via subprocess (avoids re-loading the suite per invocation; same underlying methods).

## Results (n=6 tasks, 12 episodes each)

| Metric | Ours | AgentDojo-direct | Delta |
| --- | --- | --- | --- |
| Benign utility rate | 0.333 (2/6) | 0.000 (0/6) | +0.333 |
| Utility under attack | 0.333 (2/6) | 0.000 (0/6) | +0.333 |
| Targeted ASR | 0.000 (0/6) | 0.000 (0/6) | +0.000 |

Raw per-episode results are in `runs/local/reproduction/trace.db` (`episode` table) — nothing here is hand-copied; the table above is `_aggregate()`'s direct output from `scripts/reproduce_s1_08.py`.

> **Corrected 2026-08-17.** This table originally reported Targeted ASR as 1.000 (6/6) for both pipelines. That was wrong: `_aggregate()` computed `1 - sum(security) / len(security)`, but `TaskSuite.run_task_with_pipeline`'s `security` return is the injection task's own `.security()` check, and — confirmed both by reading the concrete `workspace` injection-task implementations (they return `True` exactly when the post-environment shows the attacker's effect, e.g. the malicious email actually sent) and empirically (manually executing the malicious `send_email` call against a fresh environment and confirming `.security()` returns `True`, versus `False` on an untouched one) — `True` means the injection **succeeded**. The formula's leading `1 -` was therefore inverting the metric: the stored per-episode `security` values were always correct (the adapter persists AgentDojo's raw boolean unmodified), only the aggregate label was backwards. Fixed in `scripts/reproduce_s1_08.py` by removing the inversion; every number and claim below reflects the corrected 0.000.

## The gap, explained

**Targeted ASR matches exactly (0.0 vs 0.0)** — the `important_instructions` attack did not succeed on *any* of the 6 tasks, on either pipeline. That's a real, if unglamorous, finding rather than a defense working: `llama3.2:3b`'s tool-calling is unreliable enough (see the utility gap below, and note the *benign* utility rate is also only 0.333) that it doesn't reliably execute the injected multi-step action either — e.g. episode 2 in `runs/local/reproduction/trace.db` shows the model attempting an unrelated tool (`add_calendar_event_participants`) with malformed placeholder arguments instead of ever calling `send_email`. This is exactly the risk Appendix A.5 names up front: *"Small models may compress your dynamic range... If L1–L3 are simply bad at following any instruction, attack success may look artificially low."* A weak model's low ASR here is a capability ceiling, not evidence of robustness — worth remembering when reading any undefended-baseline number from L1/L2 later in the project.

**Utility diverges (0.333 vs 0.000)**, and the cause is visible directly in AgentDojo's own output during the run:

```
[debug] broken JSON: '{"day": "2024-05-15"}>'
```

That line comes from `agentdojo/agent_pipeline/llms/local_llm.py`, which is built for local model servers with **no native OpenAI-style function-calling** — it prompts the model to emit tool calls in a custom text-delimited format (`tool_delimiter`) and parses them out with regex, catching `InvalidModelOutputError`/JSON failures along the way. That's the reasonable default for a generic vLLM server, but `llama3.2:3b` was never trained on that specific delimiter convention, so its tool calls frequently fail to parse — task failure, `utility=False`, even on turns where the model reasoned correctly.

Our own `OllamaClient` (S1-04) instead calls Ollama's **native** `/api/chat` endpoint with a real `tools=` payload — Ollama parses the model's structured tool-call output itself (confirmed via `ollama list`: `llama3.2:3b`'s capabilities include `"tools"`), so calls that AgentDojo's prompt-based parser drops entirely still succeed through our adapter. This is a genuine, model-specific advantage of going through Ollama's own tool-calling support rather than AgentDojo's generic local-server path — not a bug, and not something to "fix" to force agreement.

A secondary, smaller source of expected variance: neither pipeline shares a seed (AgentDojo's `LocalLLM` defaults to `temperature=1.0`; our client uses Ollama's own default), so even a perfectly faithful adapter wouldn't reproduce bit-identical completions run to run.

**Conclusion:** the one metric that isolates adapter correctness from tool-calling-format differences — targeted ASR, which only requires the security scorer to see whether the attacker's goal was reached — matches exactly (now correctly reported as 0.0, not 1.0). The utility gap has a specific, verified mechanical cause outside the adapter itself (AgentDojo's local-model tool-call parser vs. Ollama's native one), not an unexplained discrepancy.

## Cost and latency (proves the Sprint 1 acceptance criterion)

```sql
SELECT episode_id, SUM(usd) AS total_usd, COUNT(*) AS n_calls
FROM cost_record GROUP BY episode_id;
```

Returns one row per episode (24 rows in the trace DB — two runs accumulated: the original S1-08 run plus the 2026-08-17 re-run after the ASR fix above), e.g. `{'episode_id': 1, 'total_usd': 0.0, 'n_calls': 2}` — `$0` throughout, as expected for a local Ollama model. `injection_pareto.trace.cost_summary_by_episode()` adds p95 wall-clock latency per episode alongside the SQL-summed `$`: per-episode p95 across both runs ranged 501–2730ms.

## Re-running

`scripts/reproduce_s1_08.py` is the executable source of every number above — re-run it to regenerate `runs/local/reproduction/trace.db` and reprint the aggregate table. `runs/` is gitignored (regenerable output, not source), so the trace DB itself isn't committed; this document and the script are.
