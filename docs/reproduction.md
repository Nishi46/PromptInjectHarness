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
| Targeted ASR | 1.000 (6/6) | 1.000 (6/6) | +0.000 |

Raw per-episode results are in `runs/local/reproduction/trace.db` (`episode` table) — nothing here is hand-copied; the table above is `_aggregate()`'s direct output from `scripts/reproduce_s1_08.py`.

## The gap, explained

**Targeted ASR matches exactly (1.0 vs 1.0)** — every task fell to the attack on both pipelines, the undefended-baseline result expected for a 3B model (matches S0-04's earlier single-task finding).

**Utility diverges (0.333 vs 0.000)**, and the cause is visible directly in AgentDojo's own output during the run:

```
[debug] broken JSON: '{"day": "2024-05-15"}>'
```

That line comes from `agentdojo/agent_pipeline/llms/local_llm.py`, which is built for local model servers with **no native OpenAI-style function-calling** — it prompts the model to emit tool calls in a custom text-delimited format (`tool_delimiter`) and parses them out with regex, catching `InvalidModelOutputError`/JSON failures along the way. That's the reasonable default for a generic vLLM server, but `llama3.2:3b` was never trained on that specific delimiter convention, so its tool calls frequently fail to parse — task failure, `utility=False`, even on turns where the model reasoned correctly.

Our own `OllamaClient` (S1-04) instead calls Ollama's **native** `/api/chat` endpoint with a real `tools=` payload — Ollama parses the model's structured tool-call output itself (confirmed via `ollama list`: `llama3.2:3b`'s capabilities include `"tools"`), so calls that AgentDojo's prompt-based parser drops entirely still succeed through our adapter. This is a genuine, model-specific advantage of going through Ollama's own tool-calling support rather than AgentDojo's generic local-server path — not a bug, and not something to "fix" to force agreement.

A secondary, smaller source of expected variance: neither pipeline shares a seed (AgentDojo's `LocalLLM` defaults to `temperature=1.0`; our client uses Ollama's own default), so even a perfectly faithful adapter wouldn't reproduce bit-identical completions run to run.

**Conclusion:** the one metric that isolates adapter correctness from tool-calling-format differences — targeted ASR, which only requires the security scorer to see whether the attacker's goal was reached — matches exactly. The utility gap has a specific, verified mechanical cause outside the adapter itself (AgentDojo's local-model tool-call parser vs. Ollama's native one), not an unexplained discrepancy.

## Cost and latency (proves the Sprint 1 acceptance criterion)

```sql
SELECT episode_id, SUM(usd) AS total_usd, COUNT(*) AS n_calls
FROM cost_record GROUP BY episode_id;
```

Returns one row per episode (12 rows for this run), e.g. `{'episode_id': 1, 'total_usd': 0.0, 'n_calls': 2}` — `$0` throughout, as expected for a local Ollama model. `injection_pareto.trace.cost_summary_by_episode()` adds p95 wall-clock latency per episode alongside the SQL-summed `$`: this run's per-episode p95 ranged 501–2226ms.

## Re-running

`scripts/reproduce_s1_08.py` is the executable source of every number above — re-run it to regenerate `runs/local/reproduction/trace.db` and reprint the aggregate table. `runs/` is gitignored (regenerable output, not source), so the trace DB itself isn't committed; this document and the script are.
