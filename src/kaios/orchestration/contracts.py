"""Structured human request and CEO response contracts."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from kaios.models import ModelProviderName


class CEOResponseStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CEORequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, str_strip_whitespace=True
    )

    workspace_id: str = Field(min_length=1)
    request_type: str = Field(min_length=1)
    seed: str | None = Field(default=None, min_length=1)
    research_objective: str | None = Field(default=None, min_length=1)
    marketplace: str = Field(default="etsy", min_length=1)
    result_limit: int = Field(default=12, ge=1, le=100)
    report_output_location: str | None = Field(default=None, min_length=1)
    model_provider: ModelProviderName | None = None


class CEOResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_id: str
    request_type: str
    status: CEOResponseStatus
    parent_task_id: str
    child_task_id: str
    specialist_result_id: str | None = None
    ceo_result_id: str
    decision_id: str | None = None
    summary: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    requires_human_approval: bool
