"""Deterministic offline model provider for early KAIOS development."""

from __future__ import annotations

from typing import Any

from .base import ModelOutput, ModelProvider, ModelRequest


class RulesModelProvider(ModelProvider):
    """Produce conservative Product Intelligence output without an AI service."""

    @property
    def provider_id(self) -> str:
        return "rules"

    @property
    def requires_network(self) -> bool:
        return False

    @property
    def requires_paid_access(self) -> bool:
        return False

    def generate(self, request: ModelRequest) -> ModelOutput:
        evidence = request.input_data.get("evidence", [])
        seed = str(request.input_data.get("seed", "Product")).strip() or "Product"
        if not isinstance(evidence, list):
            evidence = []

        opportunities = [
            self._opportunity(item, seed, len(evidence))
            for item in evidence[:12]
            if isinstance(item, dict)
        ]
        return ModelOutput(data=opportunities, provider_id=self.provider_id)

    def _opportunity(
        self, evidence: dict[str, Any], seed: str, evidence_count: int
    ) -> dict[str, Any]:
        title = str(evidence.get("title") or f"{seed.title()} Opportunity").strip()
        content = str(evidence.get("content") or "").strip()
        url = str(evidence.get("url") or "").strip()
        metadata = evidence.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        content_lower = content.lower()
        demand_terms = ("bestseller", "popular", "trending", "high demand", "reviews")
        demand_score = sum(term in content_lower for term in demand_terms)
        if demand_score >= 2:
            demand_signal = "High"
        elif demand_score == 1 or len(content) >= 80:
            demand_signal = "Medium"
        else:
            demand_signal = "Low"

        has_source_detail = bool(url or content)
        confidence = "Medium" if has_source_detail else "Low"
        if url and len(content) >= 200:
            confidence = "High"

        price_range = str(
            metadata.get("price_range")
            or evidence.get("price_range")
            or "Requires marketplace validation"
        )
        competitor_estimate = str(
            metadata.get("competitor_count_estimate")
            or evidence.get("competitor_count_estimate")
            or f"Unknown; {evidence_count} offline evidence items reviewed"
        )
        recommended = confidence != "Low" and demand_signal != "Low"

        return {
            "title": title,
            "evidence_urls": [url] if url else [],
            "price_range": price_range,
            "competitor_count_estimate": competitor_estimate,
            "demand_signal": demand_signal,
            "profitability_hint": (
                "Potential opportunity; validate production cost and Etsy fees"
            ),
            "confidence": confidence,
            "recommended": recommended,
            "why_recommended": (
                "Deterministic offline rules found usable evidence and a demand signal."
                if recommended
                else "Insufficient offline evidence; collect more marketplace data."
            ),
        }
