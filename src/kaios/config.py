from pathlib import Path
from typing import Optional
import os

import yaml
from dotenv import load_dotenv

from .models import ReportConfig


DEFAULTS = {
    "marketplace": "etsy",
    "default_search_limit": 12,
    "output_dir": "reports",
    "confidence_threshold": "Medium",
    "model": "gpt-4o-mini",
}


def load_config(path: Optional[str] = None) -> ReportConfig:
    load_dotenv()
    cfg = dict(DEFAULTS)
    if path and Path(path).exists():
        with open(path) as f:
            user = yaml.safe_load(f) or {}
        cfg.update({k: v for k, v in user.items() if v is not None})
    if os.getenv("KAIOS_LLM_MODEL"):
        cfg["model"] = os.environ["KAIOS_LLM_MODEL"]
    return ReportConfig(**cfg)
