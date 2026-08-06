"""Deterministic evidence fixtures for the network-free Phase 1 demo."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kaios.models import Opportunity, ReportConfig
from kaios.reporter import write_reports


OFFLINE_DEMO_SOURCE_TYPE = "MOCK / OFFLINE DEMO"


class OfflineDemoExtractor:
    """Return fixed development evidence and never contact an external service."""

    def __init__(self, marketplace: str = "etsy", limit: int = 12) -> None:
        self.marketplace = marketplace
        self.limit = limit

    def gather(self, seed: str) -> list[dict[str, Any]]:
        normalized_seed = " ".join(seed.split())
        evidence = [
            {
                "title": f"Personalized {normalized_seed.title()} Gift",
                "url": "offline-demo://evidence/personalized-gift",
                "content": (
                    "Mock bestseller signal with popular gift intent, review activity, "
                    "and a personalization angle. This is synthetic demo evidence."
                ),
                "source": "KAIOS deterministic demo fixture",
                "source_url": "offline-demo://fixtures/product-intelligence",
                "source_type": OFFLINE_DEMO_SOURCE_TYPE,
                "metadata": {
                    "price_range": "Mock range: £18-£28",
                    "competitor_count_estimate": "Mock estimate: medium competition",
                },
            },
            {
                "title": f"Minimalist {normalized_seed.title()} Design",
                "url": "offline-demo://evidence/minimalist-design",
                "content": (
                    "Synthetic trending and high demand signals for a minimalist niche. "
                    "No marketplace was queried."
                ),
                "source": "KAIOS deterministic demo fixture",
                "source_url": "offline-demo://fixtures/product-intelligence",
                "source_type": OFFLINE_DEMO_SOURCE_TYPE,
                "metadata": {
                    "price_range": "Mock range: £14-£24",
                    "competitor_count_estimate": "Mock estimate: high competition",
                },
            },
            {
                "title": f"Niche {normalized_seed.title()} Collection",
                "url": "offline-demo://evidence/niche-collection",
                "content": (
                    "Synthetic low-confidence niche observation for comparison. "
                    "This record is for offline workflow testing only."
                ),
                "source": "KAIOS deterministic demo fixture",
                "source_url": "offline-demo://fixtures/product-intelligence",
                "source_type": OFFLINE_DEMO_SOURCE_TYPE,
                "metadata": {
                    "price_range": "Mock range: £12-£20",
                    "competitor_count_estimate": "Mock estimate: unknown competition",
                },
            },
        ]
        return evidence[: self.limit]


def write_offline_demo_reports(
    seed: str, opportunities: list[Opportunity], config: ReportConfig
) -> tuple[Path, Path]:
    """Write legacy-compatible reports with unmistakable offline labelling."""

    markdown_path, json_path = write_reports(seed, opportunities, config)
    disclaimer = (
        "> **Evidence mode: MOCK / OFFLINE DEMO.** This is deterministic fixture "
        "data, not live marketplace research.\n\n"
    )
    markdown_path.write_text(
        disclaimer + markdown_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    report_data = json.loads(json_path.read_text(encoding="utf-8"))
    report_data.update(
        {
            "evidence_mode": OFFLINE_DEMO_SOURCE_TYPE,
            "disclaimer": "Deterministic fixture data; not live marketplace research.",
        }
    )
    json_path.write_text(
        json.dumps(report_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return markdown_path, json_path
