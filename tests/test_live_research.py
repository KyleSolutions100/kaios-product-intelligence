from __future__ import annotations

import json
from pathlib import Path

import httpx

from kaios.core.contracts import ResultStatus, TaskStatus
from kaios.repositories.sqlite import SQLiteRepositories
from kaios.runtime import build_live_research_runtime
from kaios.sources import EtsyProvider


def _live_response(results=None, count=None):
    if results is None:
        results = [
            {
                "listing_id": 1001,
                "title": "Live Public Dog Owner Shirt",
                "url": "https://www.etsy.com/listing/1001/dog-owner-shirt",
                "price": {"amount": 2200, "divisor": 100, "currency_code": "GBP"},
                "num_favorers": 45,
                "tags": ["dog owner", "dog shirt"],
                "shop": {"shop_id": 99, "shop_name": "ExamplePublicShop", "review_count": 80},
                "images": [{"url_fullxfull": "https://i.etsystatic.com/example.jpg"}],
            }
        ]
    return {"count": len(results) if count is None else count, "results": results}


def _patch_provider(monkeypatch, payload):
    requests = []

    def handler(request):
        requests.append(request)
        if isinstance(payload, Exception):
            raise payload
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = EtsyProvider(
        "test-key:test-secret", client=client, max_retries=0, sleep=lambda value: None
    )
    monkeypatch.setenv("ETSY_API_KEY", "test-key:test-secret")
    monkeypatch.setattr(
        "kaios.sources.factory.EtsyProvider", lambda api_key: provider
    )
    return requests


def test_live_research_uses_rules_and_persists_full_evidence(monkeypatch, tmp_path):
    requests = _patch_provider(monkeypatch, _live_response(count=123))
    database_path = tmp_path / "kaios.db"
    runtime = build_live_research_runtime(database_path)
    runtime.ensure_default_workspace()

    response = runtime.submit_product_research(
        workspace_id="print-on-demand",
        seed="dog owner shirt",
        result_limit=5,
        report_output_location=str(tmp_path / "reports"),
    )

    assert response.status.value == "succeeded"
    assert len(requests) == 1
    specialist = runtime.repositories.results.get(
        "print-on-demand", response.specialist_result_id
    )
    assert specialist.status is ResultStatus.SUCCEEDED
    assert specialist.data["provider"] == "rules"
    assert specialist.data["evidence_retrieval"]["source_type"] == "LIVE"
    assert specialist.evidence[0]["listing_id"] == "1001"
    assert specialist.evidence[0]["price"]["amount"] == "22"
    assert specialist.evidence[0]["review_scope"] == "SHOP"
    assert specialist.evidence[0]["image_references"][0]["url"].startswith(
        "https://i.etsystatic.com/"
    )
    markdown_report = Path(specialist.data["reports"]["markdown"])
    json_report = Path(specialist.data["reports"]["json"])
    assert "Evidence mode: LIVE" in markdown_report.read_text(encoding="utf-8")
    report_data = json.loads(json_report.read_text(encoding="utf-8"))
    assert report_data["evidence_mode"] == "LIVE"
    assert "not verified sales" in report_data["disclaimer"]
    restarted = SQLiteRepositories(database_path)
    assert restarted.results.get(
        "print-on-demand", response.specialist_result_id
    ) == specialist


def test_live_success_with_zero_listings_completes_without_fabrication(
    monkeypatch, tmp_path
):
    _patch_provider(monkeypatch, _live_response(results=[], count=0))
    runtime = build_live_research_runtime(tmp_path / "kaios.db")
    runtime.ensure_default_workspace()

    response = runtime.submit_product_research(
        workspace_id="print-on-demand",
        seed="extremely exact no match phrase",
        report_output_location=str(tmp_path / "reports"),
    )

    assert response.status.value == "succeeded"
    specialist = runtime.repositories.results.get(
        "print-on-demand", response.specialist_result_id
    )
    assert specialist.evidence == []
    assert specialist.recommendations == []
    assert specialist.data["zero_opportunities"] is True
    assert specialist.data["evidence_retrieval"] == {
        "succeeded": True,
        "source_type": "LIVE",
        "marketplace": "etsy",
        "total_available": 0,
        "retrieved_at": specialist.data["evidence_retrieval"]["retrieved_at"],
    }


def test_network_failure_fails_child_and_parent_without_decision(monkeypatch, tmp_path):
    request = httpx.Request("GET", "https://openapi.etsy.com")
    _patch_provider(monkeypatch, httpx.ConnectError("offline", request=request))
    runtime = build_live_research_runtime(tmp_path / "kaios.db")
    runtime.ensure_default_workspace()

    response = runtime.submit_product_research(
        workspace_id="print-on-demand",
        seed="dog owner shirt",
        report_output_location=str(tmp_path / "reports"),
    )

    assert response.status.value == "failed"
    assert response.decision_id is None
    assert "MarketplaceNetworkError" in response.error
    tasks = runtime.repositories.tasks.list("print-on-demand")
    assert {task.status for task in tasks} == {TaskStatus.FAILED}
    assert runtime.repositories.decisions.list("print-on-demand") == []
    assert not (tmp_path / "reports").exists()
    assert all(
        runtime.repositories.events.list_for_task("print-on-demand", task.task_id)
        for task in tasks
    )
