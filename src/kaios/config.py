from pathlib import Path
from typing import Optional
import os

import yaml
from dotenv import load_dotenv

from .models import ReportConfig
from .model_providers.factory import normalize_provider_name


DEFAULTS = {
    "marketplace": "etsy",
    "default_search_limit": 12,
    "output_dir": "reports",
    "confidence_threshold": "Medium",
    "model_provider": "rules",
    "agent_model_providers": {},
    "model": "gpt-4o-mini",
}


def load_config(path: Optional[str] = None) -> ReportConfig:
    load_dotenv()
    cfg = {**DEFAULTS, "agent_model_providers": dict(DEFAULTS["agent_model_providers"])}
    if path and Path(path).exists():
        with open(path) as f:
            user = yaml.safe_load(f) or {}
        cfg.update({k: v for k, v in user.items() if v is not None})
    if os.getenv("KAIOS_LLM_MODEL"):
        cfg["model"] = os.environ["KAIOS_LLM_MODEL"]
    configured_provider = os.getenv("KAIOS_MODEL_PROVIDER") or os.getenv(
        "KAIOS_LLM_PROVIDER"
    )
    if configured_provider:
        cfg["model_provider"] = normalize_provider_name(configured_provider)
    agent_provider = os.getenv("KAIOS_PRODUCT_INTELLIGENCE_MODEL_PROVIDER")
    if agent_provider:
        cfg["agent_model_providers"]["product_intelligence"] = (
            normalize_provider_name(agent_provider)
        )
    return ReportConfig(**cfg)
