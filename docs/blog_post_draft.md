# What actually happens when you try to break your own prompt-injection defenses

*Draft — publishing destination and timing are the author's call, not
decided here.*

I spent seven sprints building `injection-pareto`, a benchmark for
prompt-injection defenses in LLM agents: static attacks, an LLM-driven
adaptive mutation loop that rewrites its own payloads round over round,
defense composition, and a 6-tier model capability ladder spanning four
providers. The whole thing ran for **$0.0129** — thirteen hundredths of a
cent — across **8,063 real episodes**, because the constraint that actually
matters for a benchmark like this isn't dollars, it's whether the results
still mean anything a year from now. Every local-model number in this post
uses open-weight models pinned by digest, not a version string, so they
stay reproducible indefinitely.

Here's what I actually found — not what I expected to find going in.

## Claim 1: the null result is the finding

I built the adaptive loop expecting it to do what adaptive attack loops are
supposed to do: reveal that a defense which looks solid under static
testing is actually shakier than it appears, once an attacker gets to
iterate. That's the standard story in this line of research, and it's why
Sprint 4 of this project was scoped around exactly that claim.

It didn't happen. Not "didn't happen much" — didn't happen at all. Across
every local, digest-pinned model in this benchmark, static ASR is `0.000`
*and* adaptive ASR@20 is `0.000`, across 360 real adaptive trials and every
static cell I've ever measured. Twenty rounds of real LLM-driven payload
mutation, on every defense, against every attack family, found nothing
that static evaluation hadn't already blocked.

That's not a failure of the adaptive loop — I checked. The loop itself
works; it found genuine drift-and-retry behavior, and (see below) it did
land one real compromise elsewhere in the project. It's a real, if
slightly deflating, finding about the specific models tested: they're
already so far below whatever threshold makes a defense's ranking
"misleading" that there's no daylight between static and adaptive
measurement to have been misleading about. The honest claim is a shared
floor, not a shifted ranking.

## Claim 2: a real, if narrow, security frontier

The one place I ever measured a non-zero attack success rate in this
entire project is a single paid, 120-billion-parameter open-weight model
on Groq. On that one model, a real Pareto frontier shows up:
`instructional_prevention` and `tool_allowlist` both block the one
measured real attack completely — at `$0` marginal cost, since neither
defense makes an extra model call. A third, mechanistically different
defense (`spotlighting`, a prompt-rewriting technique) measured *zero*
benefit on that same signal — identical attack success rate to running no
defense at all. Worth saying plainly rather than assuming a structurally
different defense must have helped: on this data, it didn't.

This is a narrow finding — one model, small sample sizes — and I say so
directly in the project's own limitations section. But it's a real
frontier, not an assumed one, and it's the only place in the whole project
a security/utility/cost tradeoff with actual spread exists to plot.

## Claim 3: composition isn't free, even when the headline metric can't show it

Stacking defenses is supposed to be a strict improvement, or at worst
free. I found a real counterexample — not in attack-success-rate, but one
layer downstream of it. Composing `spotlighting` *ahead of* `guard_model`
(a small classifier model that screens tool outputs for injected
instructions) measurably and consistently degrades the guard's own
classification input. I checked this across 7 real matched pairs — same
suite, same model, same task, same attack, differing only in whether
`spotlighting` ran first — and all 7 show the same direction.

It doesn't move the project's ASR numbers, because `guard_model`'s block
verdict is a documented no-op in this benchmark today — a real, honestly
stated limitation, not a hidden one. But it's a genuine interaction effect
a composition matrix built purely from each defense's own solo numbers
would have missed completely. If you're stacking a prompt-rewriting
defense ahead of a classifier-based one, check what the classifier is
actually seeing.

## The best bug story: finding a real bug in someone else's benchmark

The most satisfying moment of this project wasn't a result — it was a
crash. The first full 60-trial run of the adaptive loop crashed **27 of 60
trials — 45%** — with a YAML parse error, deep inside AgentDojo's own
environment-injection code (AgentDojo is the underlying agent-benchmark
framework this project builds on for its primary task suite).

Root-causing it (not just retrying and hoping) turned up something
structural: AgentDojo builds its simulated environment by substituting
attack payloads straight into raw YAML *source text* via `str.format`,
then parsing the result. Every attack this project had used before the
adaptive loop was a small set of hand-authored templates that happened
never to contain a YAML-breaking character. An LLM inventing free text
round after round has no such guarantee — an unescaped quote in a
mutated payload is enough to break the surrounding YAML scalar and crash
the parse before the agent ever runs.

That's not a bug in my code. It's a real fragility in a framework other
people are already using, that only ever surfaces once you feed it
genuinely adversarial, model-generated input instead of hand-picked
strings — which is exactly the kind of input an adaptive attack loop is
for. The fix on my end was straightforward once the cause was clear:
treat a round that fails to execute as "no compromise, keep going" instead
of letting one bad payload abort an entire 20-round trial. Re-running
after the fix: zero YAML failures, down from 27.

(There were other real bugs along the way worth a mention: a fixed-grid
AUC computation that came out *worse than random* — 0.423 — purely as a
measurement artifact of where the grid's threshold points happened to
land relative to the classifier's own tied scores, versus the real,
correct number underneath it, 0.875, caught by cross-checking two
independent computations against each other rather than trusting the
first one. And a Gemini integration that worked fine on a minimal smoke
test and then broke on the real adapter's full tool schema, twice, for
two different undocumented reasons. The full list is in
`docs/retros/synthesis.md`.)

## How it's built

Two task suites: AgentDojo's workspace tasks, and a second suite I built
from scratch specifically because AgentDojo doesn't model an attack vector
I wanted to test — injection through a tool's *description* (the schema an
agent reads before ever calling anything), not just its *output*. Eight
defenses spanning three mechanism families (prompt-level, detection-only,
architectural hard-block), a 4-hook `Defense` protocol they all share, and
composition support with real per-layer trace attribution so a stacked
pair's cost and verdicts are attributable to the specific member that
produced them. Every number in every table is a live query against a
SQLite trace database — never hand-typed. Full design log, including every
decision and every dead end: `documentation/sprint_planning.md`.

## Reproducing this

`make reproduce` re-runs the exact local-only sweep behind the project's
static-baseline table — no API keys, $0, digest-pinned open-weight models.
Real, measured wall-clock: 7–10 minutes, confirmed with two independent
live runs in clean checkouts, not a single estimate. A second run finishes
in about 3 seconds, since it recognizes the work is already done.

## What this doesn't show

Stated plainly, not buried: this project's adaptive attacker never had a
budget beyond 20 rounds, and was never run against a hosted or frontier
model at all, for cost reasons. Both task suites are simulated
environments, not production systems. Hosted-model results can drift or
disappear — one model this project depended on was retired mid-project,
which is exactly why the local numbers are the ones pinned by digest. The
full limitations list, with the specifics behind each one, is in the
project's own README.

---

Code, results, and the full sprint-by-sprint design log:
[github.com/Nishi46/PromptInjectHarness](https://github.com/Nishi46/PromptInjectHarness)
