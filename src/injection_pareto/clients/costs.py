from __future__ import annotations

from injection_pareto.types import CostRecord

# Published list-price rates (USD per 1M tokens), for cost-modeling only.
# Every model actually used in the $0 execution track (sprint_planning.md
# Appendix A) runs on a free tier, so real spend is $0 regardless of what
# this table says — it exists so the trace DB can still report a meaningful
# $/task figure (e.g. for the Sprint 7 Pareto plot) if a paid tier is ever
# swapped in. Keyed by the exact model string used in configs/models.yaml;
# add an entry here whenever a new hosted model is pinned.
_RATES_PER_MILLION_TOKENS: dict[str, tuple[float, float]] = {
    # llama-3.3-70b-versatile retired from Groq's catalog between the L5
    # pick (2026-08-16, configs/models.yaml) and S2-11's sweep (2026-08-18)
    # -- exactly the hosted-model drift risk Appendix A warns about. L5 was
    # repointed at openai/gpt-oss-120b (confirmed via a real tool-calling
    # request against Groq's API on 2026-08-18); rate per Groq's own docs
    # (console.groq.com/docs/model/openai/gpt-oss-120b).
    "openai/gpt-oss-120b": (0.15, 0.60),  # Groq
    "gemini-3.5-flash": (0.075, 0.30),  # Google AI Studio
    # OpenRouter's `:free` suffix models are genuinely $0 -- not a modeled
    # list price the way the two rates above are, an actual real rate.
    # S6-05's L6 pick (`configs/models.yaml`); OpenRouter's own "rotating
    # free model" framing (Appendix A.1) means this entry may need
    # updating whenever L6's pick changes.
    "nvidia/nemotron-3.5-lightning:free": (0.0, 0.0),  # OpenRouter
}


def compute_cost(*, provider: str, model: str, tokens_in: int, tokens_out: int) -> CostRecord:
    if provider == "ollama":
        return CostRecord(usd=0.0, tokens_in=tokens_in, tokens_out=tokens_out)

    if model not in _RATES_PER_MILLION_TOKENS:
        raise ValueError(
            f"No published rate for model {model!r} (provider={provider!r}); "
            "add it to _RATES_PER_MILLION_TOKENS in clients/costs.py."
        )
    rate_in, rate_out = _RATES_PER_MILLION_TOKENS[model]
    usd = (tokens_in / 1_000_000) * rate_in + (tokens_out / 1_000_000) * rate_out
    return CostRecord(usd=usd, tokens_in=tokens_in, tokens_out=tokens_out)
