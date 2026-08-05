import json
from datetime import date
from pathlib import Path
from typing import List

from .models import Opportunity, ReportConfig


def render_md(seed: str, opps: List[Opportunity], cfg: ReportConfig) -> str:
    lines = [
        f"# KAIOS — Product Intelligence Report",
        f"",
        f"- **Date:** {date.today().isoformat()}",
        f"- **Marketplace:** {cfg.marketplace}",
        f"- **Seed:** {seed}",
        f"- **Candidates:** {len(opps)}",
        f"",
        f"## Opportunities",
        f"",
    ]
    for i, o in enumerate(opps, 1):
        flag = "✅ **Recommended for CEO review**" if o.recommended else "👁️ Watch"
        lines.append(f"### {i}. {o.title}")
        lines.append(f"- **Confidence:** {o.confidence} | {flag}")
        lines.append(f"- **Price range:** {o.price_range}")
        lines.append(f"- **Competitors:** {o.competitor_count_estimate}")
        lines.append(f"- **Demand signal:** {o.demand_signal}")
        lines.append(f"- **Profitability:** {o.profitability_hint}")
        lines.append(f"- **Evidence:**")
        for url in o.evidence_urls[:5]:
            lines.append(f"  - {url}")
        lines.append("")
    return "\n".join(lines)


def render_json(seed: str, opps: List[Opportunity], cfg: ReportConfig) -> dict:
    return {
        "date": date.today().isoformat(),
        "marketplace": cfg.marketplace,
        "seed": seed,
        "count": len(opps),
        "opportunities": [o.model_dump() for o in opps],
    }


def write_reports(seed: str, opps: List[Opportunity], cfg: ReportConfig):
    slug = seed.lower().replace(" ", "-")[:40]
    folder = Path(cfg.output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    md_path = folder / f"{date.today().isoformat()}-{slug}.md"
    json_path = folder / "latest.json"
    md_path.write_text(render_md(seed, opps, cfg))
    json_path.write_text(json.dumps(render_json(seed, opps, cfg), indent=2))
    return md_path, json_path
