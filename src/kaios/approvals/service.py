"""Workspace-safe action proposal, human approval, and simulation workflow."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

from kaios.core.contracts import (
    ActionProposal,
    AgentTask,
    ApprovalRequest,
    ApprovalStatus,
    TaskEvent,
    TaskStatus,
)
from kaios.core.lifecycle import transition_task
from kaios.core.workspaces import (
    RelationshipValidationError,
    validate_approval_for_proposal,
)
from kaios.repositories.interfaces import (
    ActionProposalRepository,
    ApprovalRepository,
    EventRepository,
    RecordNotFoundError,
    TaskRepository,
)

from .contracts import (
    PendingApprovalView,
    PolicyOutcome,
    ProposalReview,
    SimulatedExecutionRecord,
)
from .policy import PolicyEngine


class ApprovalUnitOfWork(Protocol):
    tasks: TaskRepository
    proposals: ActionProposalRepository
    approvals: ApprovalRepository
    events: EventRepository

    def transaction(self) -> AbstractContextManager[ApprovalUnitOfWork]: ...


class ApprovalWorkflowError(RuntimeError):
    """Base error for safely rejected approval operations."""


class ApprovalActorError(ApprovalWorkflowError):
    """Raised when a non-human or prohibited actor attempts resolution."""


class ApprovalStateError(ApprovalWorkflowError):
    """Raised when an approval or task is in the wrong lifecycle state."""


class ApprovalExpiredError(ApprovalStateError):
    """Raised when an expired approval is resolved or used."""


class ProposalMismatchError(ApprovalWorkflowError):
    """Raised when execution details differ from the stored proposal."""


class ExecutionBlockedError(ApprovalWorkflowError):
    """Raised when default-deny policy blocks simulated execution."""


class ActionApprovalService:
    """Coordinate proposals, policy, approvals, task state, and audit events."""

    def __init__(
        self,
        repositories: ApprovalUnitOfWork,
        *,
        policy_engine: PolicyEngine | None = None,
        approval_ttl: timedelta = timedelta(hours=24),
        human_actor_ids: frozenset[str] = frozenset({"human_owner"}),
        ceo_actor_id: str = "ceo_orchestrator",
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if approval_ttl <= timedelta(0):
            raise ValueError("approval_ttl must be positive")
        if not human_actor_ids:
            raise ValueError("at least one explicit human actor is required")
        self._repositories = repositories
        self._policy = policy_engine or PolicyEngine()
        self._approval_ttl = approval_ttl
        self._human_actor_ids = human_actor_ids
        self._ceo_actor_id = ceo_actor_id
        self._now = now or (lambda: datetime.now(timezone.utc))

    def create_proposal(self, proposal: ActionProposal) -> ProposalReview:
        task = self._require_task(proposal.workspace_id, proposal.task_id)
        if task.status is not TaskStatus.RUNNING:
            raise ApprovalStateError("actions may only be proposed by a running task")
        if proposal.proposed_by_agent != task.assigned_agent:
            raise RelationshipValidationError(
                "proposal.proposed_by_agent must match the task's assigned_agent"
            )
        policy = self._policy.evaluate(proposal)
        approval: ApprovalRequest | None = None
        with self._repositories.transaction():
            self._repositories.proposals.add(proposal)
            self._event(
                task,
                "proposal_created",
                {
                    "proposal_id": proposal.proposal_id,
                    "action_type": proposal.action_type,
                    "payload_hash": proposal.approval_payload_hash(),
                },
                occurred_at=proposal.created_at,
            )
            self._event(
                task,
                "policy_evaluated",
                policy.model_dump(mode="json"),
            )
            if policy.outcome is PolicyOutcome.APPROVAL_REQUIRED:
                approval = ApprovalRequest.for_proposal(
                    proposal, requested_at=self._safe_now(task)
                )
                self._repositories.approvals.add(approval, proposal=proposal)
                self._event(
                    task,
                    "approval_requested",
                    {
                        "approval_id": approval.approval_id,
                        "proposal_id": proposal.proposal_id,
                        "payload_hash": approval.payload_hash,
                        "expires_at": (
                            approval.requested_at + self._approval_ttl
                        ).isoformat(),
                    },
                    occurred_at=approval.requested_at,
                )
                self._transition(
                    task,
                    TaskStatus.WAITING_FOR_APPROVAL,
                    details={"approval_id": approval.approval_id},
                    occurred_at=approval.requested_at,
                )
        return ProposalReview(proposal=proposal, policy=policy, approval=approval)

    def list_pending_approvals(
        self, workspace_id: str, *, at: datetime | None = None
    ) -> list[PendingApprovalView]:
        now = self._validated_time(at or self._now())
        views: list[PendingApprovalView] = []
        for approval in self._repositories.approvals.list(
            workspace_id, status=ApprovalStatus.PENDING
        ):
            proposal = self._require_proposal(workspace_id, approval.proposal_id)
            validate_approval_for_proposal(proposal, approval)
            expires_at = approval.requested_at + self._approval_ttl
            views.append(
                PendingApprovalView(
                    approval=approval,
                    proposal=proposal,
                    policy=self._policy.evaluate(proposal),
                    expires_at=expires_at,
                    is_expired=now >= expires_at,
                )
            )
        return views

    def approve(
        self,
        workspace_id: str,
        approval_id: str,
        *,
        actor_id: str,
        reason: str | None = None,
        resolved_at: datetime | None = None,
    ) -> ApprovalRequest:
        self._require_human(actor_id)
        when = self._validated_time(resolved_at or self._now())
        approval = self._require_approval(workspace_id, approval_id)
        self._ensure_pending(approval)
        if self._is_expired(approval, when):
            self.expire(workspace_id, approval_id, at=when)
            raise ApprovalExpiredError("approval expired before it could be approved")
        return self._resolve(
            approval,
            status=ApprovalStatus.APPROVED,
            actor_id=actor_id,
            reason=reason,
            when=when,
        )

    def reject(
        self,
        workspace_id: str,
        approval_id: str,
        *,
        actor_id: str,
        reason: str | None = None,
        resolved_at: datetime | None = None,
    ) -> ApprovalRequest:
        self._require_human(actor_id)
        when = self._validated_time(resolved_at or self._now())
        approval = self._require_approval(workspace_id, approval_id)
        self._ensure_pending(approval)
        if self._is_expired(approval, when):
            self.expire(workspace_id, approval_id, at=when)
            raise ApprovalExpiredError("approval expired before it could be rejected")
        return self._resolve(
            approval,
            status=ApprovalStatus.REJECTED,
            actor_id=actor_id,
            reason=reason,
            when=when,
        )

    def expire(
        self, workspace_id: str, approval_id: str, *, at: datetime | None = None
    ) -> ApprovalRequest:
        when = self._validated_time(at or self._now())
        approval = self._require_approval(workspace_id, approval_id)
        self._ensure_pending(approval)
        if not self._is_expired(approval, when):
            raise ApprovalStateError("approval has not reached its expiration time")
        return self._resolve(
            approval,
            status=ApprovalStatus.EXPIRED,
            actor_id="system_policy",
            reason="approval validity period elapsed",
            when=when,
        )

    def simulate_execution(
        self,
        proposal: ActionProposal,
        *,
        approval_id: str | None = None,
        at: datetime | None = None,
    ) -> SimulatedExecutionRecord:
        when = self._validated_time(at or self._now())
        stored = self._require_proposal(proposal.workspace_id, proposal.proposal_id)
        if stored != proposal or stored.approval_payload_hash() != proposal.approval_payload_hash():
            self._execution_blocked(
                stored,
                "proposal payload does not match the immutable stored proposal",
                when,
            )
            raise ProposalMismatchError(
                "execution payload differs from the proposal presented for approval"
            )

        policy = self._policy.evaluate(stored)
        resolved_approval: ApprovalRequest | None = None
        if policy.outcome is PolicyOutcome.BLOCKED:
            self._execution_blocked(stored, "action is blocked by policy", when)
            raise ExecutionBlockedError("action is blocked by configured policy")
        if policy.requires_approval:
            if approval_id is None:
                self._execution_blocked(stored, "human approval is required", when)
                raise ExecutionBlockedError("risky action cannot execute without approval")
            resolved_approval = self._require_approval(
                stored.workspace_id, approval_id
            )
            if resolved_approval.status is not ApprovalStatus.APPROVED:
                self._execution_blocked(
                    stored,
                    f"approval status is {resolved_approval.status.value}",
                    when,
                )
                raise ExecutionBlockedError("only an approved request may be used")
            if self._is_expired(resolved_approval, when):
                self._execution_blocked(stored, "approval has expired", when)
                raise ApprovalExpiredError("expired approval cannot be used")
            try:
                validate_approval_for_proposal(stored, resolved_approval)
            except (RelationshipValidationError, ValueError) as error:
                self._execution_blocked(stored, "approval payload does not match", when)
                raise ProposalMismatchError(
                    "approval is not bound to the exact stored proposal"
                ) from error
            if resolved_approval.resolved_by not in self._human_actor_ids:
                self._execution_blocked(
                    stored, "approval was not resolved by an explicit human", when
                )
                raise ApprovalActorError("only explicit human approval is valid")

        record = SimulatedExecutionRecord(
            workspace_id=stored.workspace_id,
            task_id=stored.task_id,
            proposal_id=stored.proposal_id,
            approval_id=(
                resolved_approval.approval_id if resolved_approval is not None else None
            ),
            action_type=stored.action_type,
            simulated_at=when,
        )
        task = self._require_task(stored.workspace_id, stored.task_id)
        with self._repositories.transaction():
            self._event(
                task,
                "simulated_execution_completed",
                record.model_dump(mode="json"),
                occurred_at=when,
            )
        return record

    def _resolve(
        self,
        approval: ApprovalRequest,
        *,
        status: ApprovalStatus,
        actor_id: str,
        reason: str | None,
        when: datetime,
    ) -> ApprovalRequest:
        event_types = {
            ApprovalStatus.APPROVED: "approval_approved",
            ApprovalStatus.REJECTED: "approval_rejected",
            ApprovalStatus.EXPIRED: "approval_expired",
        }
        destination = (
            TaskStatus.QUEUED
            if status is ApprovalStatus.APPROVED
            else TaskStatus.CANCELLED
        )
        with self._repositories.transaction():
            current = self._require_approval(
                approval.workspace_id, approval.approval_id
            )
            self._ensure_pending(current)
            proposal = self._require_proposal(
                current.workspace_id, current.proposal_id
            )
            validate_approval_for_proposal(proposal, current)
            task = self._require_task(current.workspace_id, current.task_id)
            if task.status is not TaskStatus.WAITING_FOR_APPROVAL:
                raise ApprovalStateError(
                    "approval task must be waiting for approval before resolution"
                )
            resolution_time = max(when, task.updated_at, current.requested_at)
            values = current.model_dump()
            values.update(
                status=status,
                resolved_at=resolution_time,
                resolved_by=actor_id,
                resolution_reason=reason,
            )
            resolved = ApprovalRequest.model_validate(values)
            self._repositories.approvals.update(resolved)
            self._event(
                task,
                event_types[status],
                {
                    "approval_id": resolved.approval_id,
                    "proposal_id": resolved.proposal_id,
                    "resolved_by": actor_id,
                    "reason": reason,
                },
                occurred_at=resolution_time,
            )
            self._transition(
                task,
                destination,
                details={"approval_id": resolved.approval_id},
                occurred_at=resolution_time,
            )
        return resolved

    def _transition(
        self,
        task: AgentTask,
        status: TaskStatus,
        *,
        details: dict,
        occurred_at: datetime,
    ) -> AgentTask:
        updated, event = transition_task(
            task, status, details=details, occurred_at=max(occurred_at, task.updated_at)
        )
        self._repositories.tasks.update(updated)
        self._repositories.events.add(event)
        return updated

    def _event(
        self,
        task: AgentTask,
        event_type: str,
        details: dict,
        *,
        occurred_at: datetime | None = None,
    ) -> TaskEvent:
        event = TaskEvent(
            workspace_id=task.workspace_id,
            task_id=task.task_id,
            event_type=event_type,
            details=details,
            occurred_at=max(occurred_at or self._now(), task.updated_at),
        )
        return self._repositories.events.add(event)

    def _execution_blocked(
        self, proposal: ActionProposal, reason: str, when: datetime
    ) -> None:
        task = self._require_task(proposal.workspace_id, proposal.task_id)
        with self._repositories.transaction():
            self._event(
                task,
                "execution_blocked",
                {"proposal_id": proposal.proposal_id, "reason": reason},
                occurred_at=when,
            )

    def _require_human(self, actor_id: str) -> None:
        if actor_id == self._ceo_actor_id:
            raise ApprovalActorError("the CEO orchestrator cannot approve its own proposal")
        if actor_id not in self._human_actor_ids:
            raise ApprovalActorError("only an explicitly configured human may resolve approvals")

    def _require_task(self, workspace_id: str, task_id: str) -> AgentTask:
        task = self._repositories.tasks.get(workspace_id, task_id)
        if task is None:
            raise RecordNotFoundError(
                f"task not found in workspace {workspace_id}: {task_id}"
            )
        return task

    def _require_proposal(
        self, workspace_id: str, proposal_id: str
    ) -> ActionProposal:
        proposal = self._repositories.proposals.get(workspace_id, proposal_id)
        if proposal is None:
            raise RecordNotFoundError(
                f"proposal not found in workspace {workspace_id}: {proposal_id}"
            )
        return proposal

    def _require_approval(
        self, workspace_id: str, approval_id: str
    ) -> ApprovalRequest:
        approval = self._repositories.approvals.get(workspace_id, approval_id)
        if approval is None:
            raise RecordNotFoundError(
                f"approval not found in workspace {workspace_id}: {approval_id}"
            )
        return approval

    @staticmethod
    def _ensure_pending(approval: ApprovalRequest) -> None:
        if approval.status is not ApprovalStatus.PENDING:
            raise ApprovalStateError("approval has already been resolved")

    def _is_expired(self, approval: ApprovalRequest, when: datetime) -> bool:
        return when >= approval.requested_at + self._approval_ttl

    def _safe_now(self, task: AgentTask) -> datetime:
        return max(self._validated_time(self._now()), task.updated_at)

    @staticmethod
    def _validated_time(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include timezone information")
        return value
