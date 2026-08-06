from pathlib import Path
from unittest.mock import patch
import json
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kaios.config import ReportConfig
from kaios.extractor import Extractor
from kaios.analyzer import synthesize
from kaios.model_providers import FakeModelProvider
from kaios.reporter import write_reports


class _FakeResp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class _FakeClient:
    def __init__(self, *args, **kwargs):
        self._calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url, *args, **kwargs):
        self._calls.append(("get", url))
        if "q=" in str(url):
            return _FakeResp('<a href="http://a.com">A</a><a href="http://b.com">B</a>')
        return _FakeResp("<html><head><title>A</title></head><body>Mock product content</body></html>")


def main():
    out = Path("/tmp/kaios-e2e-reports")
    cfg = ReportConfig(
        marketplace="etsy", seed="eco-friendly wedding invitations", output_dir=str(out)
    )
    extractor = Extractor(marketplace=cfg.marketplace, limit=5)

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

    with patch("kaios.sources.base.httpx.Client", _FakeClient):

        snippets = extractor.gather(cfg.seed)
        assert snippets, "No snippets gathered"

        opps = synthesize(snippets, cfg.seed, cfg.model, provider=provider)
        assert opps, "No opportunities synthesized"

        md, js = write_reports(cfg.seed, opps, cfg)
        assert md.exists(), f"Missing markdown: {md}"
        assert js.exists(), f"Missing json: {js}"

        data = json.loads(js.read_text())
        assert data["count"] == 1
        assert data["opportunities"][0]["title"] == "Mock Eco Invite"

        print("E2E passed")
        print(f"MD: {md}")
        print(f"JSON: {js}")
        print(md.read_text()[:500])


if __name__ == "__main__":
    main()
