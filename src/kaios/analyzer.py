import json
import os
from difflib import SequenceMatcher
from typing import List

from litellm import completion as litellm_completion

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


def _call_llm(snippets: List[dict], seed: str, model: str) -> str:
    user_payload = json.dumps({"seed": seed, "evidence": snippets[:12]}, ensure_ascii=False)
    response = litellm_completion(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_payload},
        ],
        api_key=os.getenv("KAIOS_LLM_API_KEY"),
        api_base=os.getenv("KAIOS_LLM_API_BASE"),
        temperature=0.2,
        max_tokens=2000,
    )
    return response.choices[0].message.content.strip()


def _clean_json(text: str) -> str:
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    return text.strip()


def dedupe(opps: List[Opportunity]) -> List[Opportunity]:
    unique: List[Opportunity] = []
    for o in opps:
        if not any(
            SequenceMatcher(None, o.title.lower(), u.title.lower()).ratio() > 0.60
            for u in unique
        ):
            unique.append(o)
    return unique


def synthesize(snippets: List[dict], seed: str, model: str) -> List[Opportunity]:
    if not snippets:
        return []
    raw = _call_llm(snippets, seed, model)
    cleaned = _clean_json(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    opps: List[Opportunity] = []
    for item in data:
        try:
            opps.append(Opportunity(**item))
        except Exception:
            continue
    return dedupe(opps)
