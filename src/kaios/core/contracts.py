"""Validated, workspace-scoped data contracts for the KAIOS runtime."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


DEFAULT_WORKSPACE_ID = "print-on-demand"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _require_aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include timezone information")
    return value


class WorkspaceStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class TaskStatus(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ResultStatus(str, Enum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class RiskClassification(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ContractModel(BaseModel):
    """Strict base model used for messages exchanged between agents."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class TimestampedContract(ContractModel):
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    _created_at_is_aware = field_validator("created_at")(_require_aware_datetime)
    _updated_at_is_aware = field_validator("updated_at")(_require_aware_datetime)

    @model_validator(mode="after")
    def updated_at_is_not_before_creation(self) -> TimestampedContract:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        return self


class Workspace(TimestampedContract):
    workspace_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    status: WorkspaceStatus = WorkspaceStatus.ACTIVE


def create_default_workspace() -> Workspace:
    """Return the initial workspace for the current POD business."""

    return Workspace(
        workspace_id=DEFAULT_WORKSPACE_ID,
        name="Print-on-Demand Store",
        description="Etsy and print-on-demand product intelligence business.",
    )


class AgentTask(TimestampedContract):
    task_id: str = Field(default_factory=lambda: _new_id("task"), min_length=1)
    workspace_id: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    assigned_agent: str = Field(min_length=1)
    requested_by: str = Field(default="human_owner", min_length=1)
    parent_task_id: str | None = None
    status: TaskStatus = TaskStatus.CREATED
    risk: RiskClassification = RiskClassification.LOW
    input_data: dict[str, Any] = Field(default_factory=dict)
    retry_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def task_cannot_parent_itself(self) -> AgentTask:
        if self.parent_task_id == self.task_id:
            raise ValueError("a task cannot be its own parent")
        return self


class ActionProposal(ContractModel):
    proposal_id: str = Field(default_factory=lambda: _new_id("proposal"), min_length=1)
    workspace_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    proposed_by_agent: str = Field(min_length=1)
    action_type: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    risk: RiskClassification = RiskClassification.LOW
    estimated_cost: Decimal = Field(default=Decimal("0"), ge=0)
    currency: str | None = None
    is_public: bool = False
    is_reversible: bool = True
    created_at: datetime = Field(default_factory=_utc_now)

    _created_at_is_aware = field_validator("created_at")(_require_aware_datetime)

    @classmethod
    def for_task(cls, task: AgentTask, **values: Any) -> ActionProposal:
        return cls(workspace_id=task.workspace_id, task_id=task.task_id, **values)

    def approval_payload_hash(self) -> str:
        """Hash the exact action details that a future approval will cover."""

        approval_payload = {
            "workspace_id": self.workspace_id,
            "task_id": self.task_id,
            "action_type": self.action_type,
            "payload": self.payload,
            "risk": self.risk.value,
            "estimated_cost": str(self.estimated_cost),
            "currency": self.currency,
            "is_public": self.is_public,
            "is_reversible": self.is_reversible,
        }
        canonical = json.dumps(
            approval_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AgentResult(ContractModel):
    result_id: str = Field(default_factory=lambda: _new_id("result"), min_length=1)
    workspace_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    status: ResultStatus = ResultStatus.SUCCEEDED
    summary: str = Field(min_length=1)
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[dict[str, Any]] = Field(default_factory=list)
    proposed_actions: list[ActionProposal] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)

    _created_at_is_aware = field_validator("created_at")(_require_aware_datetime)

    @model_validator(mode="after")
    def result_status_matches_error(self) -> AgentResult:
        if self.status is ResultStatus.FAILED and not self.error:
            raise ValueError("failed results must include an error")
        if self.status is not ResultStatus.FAILED and self.error:
            raise ValueError("only failed results may include an error")
        return self

    @classmethod
    def for_task(cls, task: AgentTask, **values: Any) -> AgentResult:
        return cls(workspace_id=task.workspace_id, task_id=task.task_id, **values)


class ApprovalRequest(ContractModel):
    approval_id: str = Field(default_factory=lambda: _new_id("approval"), min_length=1)
    workspace_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    payload_hash: str = Field(min_length=64, max_length=64)
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: datetime = Field(default_factory=_utc_now)
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    resolution_reason: str | None = None

    _requested_at_is_aware = field_validator("requested_at")(_require_aware_datetime)

    @field_validator("resolved_at")
    @classmethod
    def resolved_at_is_aware(cls, value: datetime | None) -> datetime | None:
        return _require_aware_datetime(value) if value is not None else None

    @field_validator("payload_hash")
    @classmethod
    def payload_hash_is_hex(cls, value: str) -> str:
        try:
            int(value, 16)
        except ValueError as exc:
            raise ValueError("payload_hash must be a hexadecimal SHA-256 digest") from exc
        return value.lower()

    @model_validator(mode="after")
    def resolution_fields_match_status(self) -> ApprovalRequest:
        resolution_values = (self.resolved_at, self.resolved_by, self.resolution_reason)
        if self.status is ApprovalStatus.PENDING and any(
            value is not None for value in resolution_values
        ):
            raise ValueError("pending approvals cannot contain resolution fields")
        if self.status is not ApprovalStatus.PENDING:
            if self.resolved_at is None or not self.resolved_by:
                raise ValueError("resolved approvals require resolved_at and resolved_by")
            if self.resolved_at < self.requested_at:
                raise ValueError("resolved_at cannot be earlier than requested_at")
        return self

    @classmethod
    def for_proposal(cls, proposal: ActionProposal, **values: Any) -> ApprovalRequest:
        return cls(
            workspace_id=proposal.workspace_id,
            task_id=proposal.task_id,
            proposal_id=proposal.proposal_id,
            payload_hash=proposal.approval_payload_hash(),
            **values,
        )


class DecisionRecord(ContractModel):
    decision_id: str = Field(default_factory=lambda: _new_id("decision"), min_length=1)
    workspace_id: str = Field(min_length=1)
    decision_type: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    related_task_id: str | None = None
    related_approval_id: str | None = None
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)

    _created_at_is_aware = field_validator("created_at")(_require_aware_datetime)

    @classmethod
    def for_task(cls, task: AgentTask, **values: Any) -> DecisionRecord:
        return cls(
            workspace_id=task.workspace_id,
            related_task_id=task.task_id,
            **values,
        )


class TaskEvent(ContractModel):
    event_id: str = Field(default_factory=lambda: _new_id("event"), min_length=1)
    workspace_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    from_status: TaskStatus | None = None
    to_status: TaskStatus | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=_utc_now)

    _occurred_at_is_aware = field_validator("occurred_at")(_require_aware_datetime)

    @model_validator(mode="after")
    def transition_statuses_are_consistent(self) -> TaskEvent:
        if (self.from_status is None) != (self.to_status is None):
            raise ValueError("from_status and to_status must be set together")
        if self.from_status is not None and self.from_status is self.to_status:
            raise ValueError("a transition event must change status")
        return self
