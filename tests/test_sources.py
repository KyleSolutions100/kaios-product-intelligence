from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kaios.sources import (
    EvidenceBatch,
    EtsyProvider,
    MarketplaceConfigurationError,
    MarketplaceSearchResult,
    UnsupportedMarketplaceError,
    create_marketplace_provider,
)


def test_factory_creates_only_configured_official_etsy_provider(monkeypatch):
    monkeypatch.setenv("ETSY_API_KEY", "test-key:test-secret")

    provider = create_marketplace_provider(" Etsy ")

    assert isinstance(provider, EtsyProvider)
    assert provider.marketplace_id == "etsy"
    assert provider.requires_network is True


def test_factory_does_not_fall_back_when_key_is_missing(monkeypatch):
    monkeypatch.delenv("ETSY_API_KEY", raising=False)

    with pytest.raises(MarketplaceConfigurationError, match="ETSY_API_KEY"):
        create_marketplace_provider("etsy")


def test_factory_rejects_unsupported_marketplace_without_scraping(monkeypatch):
    monkeypatch.setenv("ETSY_API_KEY", "test-key:test-secret")

    with pytest.raises(UnsupportedMarketplaceError, match="no approved official"):
        create_marketplace_provider("other-marketplace")


def test_successful_empty_search_remains_distinguishable_from_failure():
    result = MarketplaceSearchResult(
        marketplace="etsy",
        query="no matches",
        evidence=[],
        total_available=0,
        retrieved_at=datetime.now(timezone.utc),
        source_type="LIVE",
    )

    batch = EvidenceBatch(result)

    assert batch == []
    assert batch.retrieval_succeeded is True
    assert batch.total_available == 0
    assert batch.source_type == "LIVE"
