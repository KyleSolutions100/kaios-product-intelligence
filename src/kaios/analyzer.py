from difflib import SequenceMatcher
from typing import List

from .model_providers import (
    ModelProvider,
    ModelRequest,
    RulesModelProvider,
    create_model_provider,
)
from .models import Opportunity


SYSTEM_PROMPT = """\
You are a product intelligence researcher for a Print-on-Demand business.
Given structured evidence with sources, prices, demand and competition indicators,
identify distinct low-competition product opportunities.
Return ONLY a JSON array. Each item must have:
- title (str)
- evidence_urls (list[str])
- price_range (str)
- competitor_count_estimate (str)
- demand_signal (str)
- profitability_hint (str)
- confidence (High/Medium/Low)
- recommended (bool)
- why_recommended (str)
Do not include markdown fences or commentary."""


def dedupe(opps: List[Opportunity]) -> List[Opportunity]:
    unique: List[Opportunity] = []
    for o in opps:
        if not any(
            SequenceMatcher(None, o.title.lower(), u.title.lower()).ratio() > 0.60
            for u in unique
        ):
            unique.append(o)
    return unique


def synthesize(
    snippets: List[dict],
    seed: str,
    model: str | None = None,
    *,
    provider: ModelProvider | None = None,
) -> List[Opportunity]:
    """Synthesize opportunities with an injected structured model provider.

    Passing the historical third positional ``model`` argument without a provider
    selects the opt-in LiteLLM adapter. New calls without either use offline rules.
    """

    if not snippets:
        return []
    selected_provider = provider or _default_provider(model)
    output = selected_provider.generate(
        ModelRequest(
            task="product_intelligence.synthesize",
            system_prompt=SYSTEM_PROMPT,
            input_data={"seed": seed, "evidence": snippets[:12]},
            model=model,
        )
    )
    data = output.data
    if not isinstance(data, list):
        return []
    opps: List[Opportunity] = []
    for item in data:
        try:
            opps.append(Opportunity(**item))
        except Exception:
            continue
    return dedupe(opps)


def _default_provider(model: str | None) -> ModelProvider:
    if model is not None:
        return create_model_provider("litellm", model=model)
    return RulesModelProvider()
