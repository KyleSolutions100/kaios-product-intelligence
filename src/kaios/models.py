from pydantic import BaseModel
from typing import List


class Opportunity(BaseModel):
    title: str
    evidence_urls: List[str] = []
    price_range: str
    competitor_count_estimate: str
    demand_signal: str
    profitability_hint: str
    confidence: str
    recommended: bool = False
    why_recommended: str = ""


class ReportConfig(BaseModel):
    marketplace: str = "etsy"
    seed: str = ""
    search_limit: int = 12
    output_dir: str = "reports"
    confidence_threshold: str = "Medium"
    model: str = "gpt-4o-mini"
