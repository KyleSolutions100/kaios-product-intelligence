from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kaios.config import ReportConfig
from kaios.analyzer import synthesize
from kaios.model_providers import FakeModelProvider
from kaios.offline import (
    OFFLINE_DEMO_SOURCE_TYPE,
    OfflineDemoExtractor,
    write_offline_demo_reports,
)


def main():
    out = Path("/tmp/kaios-e2e-reports")
    cfg = ReportConfig(
        marketplace="etsy", seed="eco-friendly wedding invitations", output_dir=str(out)
    )
    extractor = OfflineDemoExtractor(marketplace=cfg.marketplace, limit=5)

    provider = FakeModelProvider(
        output=[
            {
                "title": "Mock Eco Invite",
                "evidence_urls": ["http://a.com"],
                "price_range": "$15–$30",
                "competitor_count_estimate": "~40 listings",
                "demand_signal": "High",
                "profitability_hint": "Good (low competition, high margin)",
                "confidence": "High",
                "recommended": True,
            }
        ]
    )

    snippets = extractor.gather(cfg.seed)
    assert snippets, "No snippets gathered"
    assert all(
        item["source_type"] == OFFLINE_DEMO_SOURCE_TYPE for item in snippets
    )

    opps = synthesize(snippets, cfg.seed, cfg.model, provider=provider)
    assert opps, "No opportunities synthesized"

    md, js = write_offline_demo_reports(cfg.seed, opps, cfg)
    assert md.exists(), f"Missing markdown: {md}"
    assert js.exists(), f"Missing json: {js}"

    data = json.loads(js.read_text())
    assert data["count"] == 1
    assert data["opportunities"][0]["title"] == "Mock Eco Invite"
    assert data["evidence_mode"] == OFFLINE_DEMO_SOURCE_TYPE
    assert "not live marketplace research" in data["disclaimer"]
    markdown = md.read_text()
    assert OFFLINE_DEMO_SOURCE_TYPE in markdown
    assert "not live marketplace research" in markdown

    print(f"Evidence mode: {OFFLINE_DEMO_SOURCE_TYPE}")
    print("This evidence is not live marketplace research.")
    print("E2E passed")
    print(f"MD: {md}")
    print(f"JSON: {js}")
    print(markdown[:500])


if __name__ == "__main__":
    main()
