from kaios.analyzer import dedupe
from kaios.model_providers import FakeModelProvider
from kaios.models import Opportunity


def test_dedupe_removes_near_duplicates():
    opps = [
        Opportunity(
            title="Apple Tee",
            evidence_urls=["http://a"],
            price_range="$20",
            competitor_count_estimate="100",
            demand_signal="high",
            profitability_hint="good",
            confidence="High",
        ),
        Opportunity(
            title="Apple T-Shirt",
            evidence_urls=["http://b"],
            price_range="$20",
            competitor_count_estimate="100",
            demand_signal="high",
            profitability_hint="good",
            confidence="High",
        ),
    ]
    result = dedupe(opps)
    assert len(result) == 1


def test_synthesize_with_injected_provider():
    provider = FakeModelProvider(
        output=[
            {
                "title": "Mock Product",
                "evidence_urls": ["http://x"],
                "price_range": "$10",
                "competitor_count_estimate": "5",
                "demand_signal": "high",
                "profitability_hint": "ok",
                "confidence": "High",
                "recommended": True,
            }
        ]
    )
    from kaios.analyzer import synthesize

    opps = synthesize(
        [{"title": "x", "url": "u", "content": "..."}],
        "test seed",
        provider=provider,
    )

    assert len(provider.calls) == 1
    assert len(opps) == 1
    assert opps[0].title == "Mock Product"
    assert opps[0].recommended is True
