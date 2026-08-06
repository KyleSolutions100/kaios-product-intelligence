from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from kaios.sources import (
    EtsyProvider,
    MarketplaceAuthenticationError,
    MarketplaceConfigurationError,
    MarketplaceNetworkError,
    MarketplaceRateLimitError,
    MarketplaceResponseError,
)
from kaios.sources.etsy import ETSY_ACTIVE_LISTINGS_URL


NOW = datetime(2026, 8, 6, 10, 30, tzinfo=timezone.utc)


def listing_payload() -> dict:
    return {
        "count": 42,
        "results": [
            {
                "listing_id": 123,
                "title": "Personalized Dog Shirt",
                "url": "https://www.etsy.com/listing/123/personalized-dog-shirt",
                "price": {"amount": 1899, "divisor": 100, "currency_code": "GBP"},
                "num_favorers": 27,
                "tags": ["dog shirt", "dog lover", "dog shirt"],
                "shop": {
                    "shop_id": 456,
                    "shop_name": "PublicDesignShop",
                    "review_count": 312,
                    "transaction_sold_count": 980,
                },
                "images": [
                    {
                        "url_fullxfull": "https://i.etsystatic.com/example/full.jpg",
                        "full_width": 2000,
                        "full_height": 1600,
                        "alt_text": "Public listing image",
                    }
                ],
            },
            {
                "listing_id": 123,
                "title": "Duplicate",
                "url": "https://www.etsy.com/listing/123/duplicate",
            },
        ],
    }


def provider_for(handler, *, max_retries=0, sleep=lambda seconds: None):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return EtsyProvider(
        "test-keystring:test-shared-secret",
        client=client,
        max_retries=max_retries,
        sleep=sleep,
        now=lambda: NOW,
    )


def test_missing_key_fails_before_any_network_request():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"count": 0, "results": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(MarketplaceConfigurationError, match="ETSY_API_KEY"):
        EtsyProvider(None, client=client)
    assert calls == 0


def test_official_get_maps_public_evidence_and_never_fetches_image_urls():
    requests: list[httpx.Request] = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=listing_payload())

    result = provider_for(handler).search("dog owner shirt", 10)

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert str(request.url).startswith(ETSY_ACTIVE_LISTINGS_URL)
    assert request.url.params["keywords"] == "dog owner shirt"
    assert request.url.params["includes"] == "Shop,Images"
    assert request.headers["x-api-key"] == "test-keystring:test-shared-secret"
    assert "authorization" not in request.headers
    assert len(result.evidence) == 1
    evidence = result.evidence[0]
    assert evidence.listing_id == "123"
    assert evidence.price.amount == "18.99"
    assert evidence.price.currency == "GBP"
    assert evidence.shop_name == "PublicDesignShop"
    assert evidence.review_count == 312
    assert evidence.review_scope == "SHOP"
    assert evidence.tags == ["dog shirt", "dog lover"]
    assert evidence.image_references[0].url.endswith("full.jpg")
    assert evidence.collected_at == NOW
    assert evidence.search_result_count == 42
    assert {signal.scope for signal in evidence.popularity_signals} == {
        "LISTING",
        "SHOP",
    }
    serialized = evidence.to_dict()
    assert serialized["source_type"] == "LIVE"
    assert "not verified listing sales" in serialized["metadata"]["popularity_disclaimer"]


def test_successful_zero_listing_response_is_not_a_source_failure():
    provider = provider_for(
        lambda request: httpx.Response(200, json={"count": 0, "results": []})
    )

    result = provider.search("an exact niche with no matches", 5)

    assert result.evidence == []
    assert result.total_available == 0
    assert result.source_type == "LIVE"


@pytest.mark.parametrize("status", [401, 403])
def test_authentication_and_permission_failures_are_typed(status):
    provider = provider_for(lambda request: httpx.Response(status, json={}))

    with pytest.raises(MarketplaceAuthenticationError) as caught:
        provider.search("dog shirt", 3)

    assert "test-keystring" not in str(caught.value)


def test_rate_limit_honours_retry_after_and_is_bounded():
    calls = 0
    sleeps: list[float] = []

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "3"}, json={})

    provider = provider_for(handler, max_retries=2, sleep=sleeps.append)

    with pytest.raises(MarketplaceRateLimitError):
        provider.search("dog shirt", 3)

    assert calls == 3
    assert sleeps == [3.0, 3.0]


def test_server_failure_retries_then_succeeds():
    calls = 0
    sleeps: list[float] = []

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={})
        return httpx.Response(200, json={"count": 0, "results": []})

    result = provider_for(handler, max_retries=1, sleep=sleeps.append).search(
        "dog shirt", 3
    )

    assert result.total_available == 0
    assert calls == 2
    assert sleeps == [1.0]


def test_timeout_is_typed_and_bounded():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out", request=request)

    provider = provider_for(handler, max_retries=1)

    with pytest.raises(MarketplaceNetworkError):
        provider.search("dog shirt", 3)
    assert calls == 2


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"count": 1, "results": {}}),
        httpx.Response(400, json={"error": "bad request"}),
    ],
)
def test_malformed_and_client_responses_are_typed(response):
    provider = provider_for(lambda request: response)

    with pytest.raises(MarketplaceResponseError):
        provider.search("dog shirt", 3)
