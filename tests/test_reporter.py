import json
from pathlib import Path
from kaios.models import Opportunity, ReportConfig
from kaios.reporter import render_md, render_json, write_reports


def test_render_md_includes_fields():
    cfg = ReportConfig(marketplace="etsy", seed="test")
    opps = [
        Opportunity(
            title="A",
            evidence_urls=["http://a"],
            price_range="$10",
            competitor_count_estimate="1",
            demand_signal="high",
            profitability_hint="ok",
            confidence="High",
            recommended=True,
        )
    ]
    md = render_md("test", opps, cfg)
    assert "# KAIOS" in md
    assert "A" in md
    assert "Recommended for CEO review" in md


def test_write_reports_creates_files(tmp_path):
    cfg = ReportConfig(marketplace="etsy", seed="test", output_dir=str(tmp_path))
    opps = [
        Opportunity(
            title="A",
            evidence_urls=["http://a"],
            price_range="$10",
            competitor_count_estimate="1",
            demand_signal="high",
            profitability_hint="ok",
            confidence="High",
        )
    ]
    md, js = write_reports("test", opps, cfg)
    assert md.exists()
    assert js.exists()
    data = json.loads(js.read_text())
    assert data["count"] == 1
    assert data["opportunities"][0]["title"] == "A"
