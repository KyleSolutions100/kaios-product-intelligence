import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kaios.sources.base import BaseSource, EtsyApiSource, WebSearchSource, EvidenceItem, build_sources


def test_api_key_env_controls_etsy_source(monkeypatch):
    monkeypatch.setenv("ETSY_API_KEY", "fake")
    sources = build_sources("etsy", 12)
    assert isinstance(sources[0], EtsyApiSource)


def test_missing_api_key_falls_back_to_web_search(monkeypatch):
    monkeypatch.delenv("ETSY_API_KEY", raising=False)
    sources = build_sources("etsy", 6)
    assert sources
    assert isinstance(sources[0], WebSearchSource)


def test_web_search_source_metadata():
    source = WebSearchSource(marketplace="etsy", limit=3)
    assert source.source_name == "Web Search"
    assert source.source_type == "compliant_web_search"
    assert source.source_url == "https://duckduckgo.com/"


def test_evidence_items_have_all_required_fields():
    item = EvidenceItem(
        title="Test Product",
        url="http://example.com",
        price="$10",
        demand="High",
        competition="40",
        source_name="Web Search",
        source_url="http://example.com",
        source_type="compliant_web_search",
    )
    d = item.to_dict()
    for key in ["title", "url", "price", "demand_signal", "competitor_count_estimate", "source", "source_url", "source_type"]:
        assert key in d
