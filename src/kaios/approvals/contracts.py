"""Structured outputs produced by the approval subsystem."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import Field, field_validator

from kaios.core.contracts import (
    ActionProposal,
    ApprovalRequest,
    ContractModel,
    RiskClassification,
)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include timezone information")
    return value


class PolicyOutcome(str, Enum):
    APPROVAL_NOT_REQUIRED = "approval_not_required"
    APPROVAL_REQUIRED = "approval_required"
    BLOCKED = "blocked"


class PolicyDecision(ContractModel):
    workspace_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    outcome: PolicyOutcome
    risk: RiskClassification
    reasons: list[str] = Field(min_length=1)
    requires_approval: bool
    allowed: bool


class ProposalReview(ContractModel):
    proposal: ActionProposal
    policy: PolicyDecision
    approval: ApprovalRequest | None = None


class PendingApprovalView(ContractModel):
    approval: ApprovalRequest
    proposal: ActionProposal
    policy: PolicyDecision
    expires_at: datetime
    is_expired: bool

    _expires_at_is_aware = field_validator("expires_at")(_aware)


class SimulatedExecutionRecord(ContractModel):
    execution_id: str = Field(
        default_factory=lambda: f"execution_{uuid4().hex}", min_length=1
    )
    workspace_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    approval_id: str | None = None
    action_type: str = Field(min_length=1)
    status: Literal["simulated"] = "simulated"
    simulated_at: datetime
    note: str = "Simulation only; no external action was performed."

    _simulated_at_is_aware = field_validator("simulated_at")(_aware)
