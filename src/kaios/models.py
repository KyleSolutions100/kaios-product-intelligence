from typing import Dict, List, Literal

from pydantic import BaseModel, Field


ModelProviderName = Literal["rules", "fake", "litellm"]


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
    model_provider: ModelProviderName = "rules"
    agent_model_providers: Dict[str, ModelProviderName] = Field(default_factory=dict)
    model: str = "gpt-4o-mini"

    def provider_for_agent(self, agent_id: str) -> ModelProviderName:
        return self.agent_model_providers.get(agent_id, self.model_provider)
