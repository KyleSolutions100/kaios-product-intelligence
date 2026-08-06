"""Workspace-boundary checks used at every relationship boundary."""

from __future__ import annotations

from typing import Iterable, Protocol, TypeVar

from .contracts import (
    ActionProposal,
    AgentResult,
    AgentTask,
    ApprovalRequest,
    DecisionRecord,
    TaskEvent,
)


class WorkspaceScoped(Protocol):
    workspace_id: str


ScopedRecord = TypeVar("ScopedRecord", bound=WorkspaceScoped)


class WorkspaceBoundaryError(ValueError):
    """Raised when records from different workspaces are combined."""


class RelationshipValidationError(ValueError):
    """Raised when related record IDs do not agree."""


def require_same_workspace(*records: WorkspaceScoped) -> str:
    if not records:
        raise ValueError("at least one workspace-scoped record is required")
    workspace_ids = {record.workspace_id for record in records}
    if len(workspace_ids) != 1:
        raise WorkspaceBoundaryError(
            f"cross-workspace relationship rejected: {sorted(workspace_ids)}"
        )
    return records[0].workspace_id


def records_for_workspace(
    records: Iterable[ScopedRecord], workspace_id: str
) -> list[ScopedRecord]:
    """Return records belonging only to the explicitly requested workspace."""

    if not workspace_id:
        raise ValueError("workspace_id is required")
    return [record for record in records if record.workspace_id == workspace_id]


def validate_task_relationship(parent: AgentTask, child: AgentTask) -> None:
    """Enforce the boundary future orchestrator child-task routing must use."""

    require_same_workspace(parent, child)
    if child.parent_task_id != parent.task_id:
        raise RelationshipValidationError(
            "child.parent_task_id must reference the supplied parent task"
        )


def validate_result_for_task(task: AgentTask, result: AgentResult) -> None:
    require_same_workspace(task, result)
    if result.task_id != task.task_id:
        raise RelationshipValidationError("result.task_id does not match task.task_id")
    for proposal in result.proposed_actions:
        validate_proposal_for_task(task, proposal)


def validate_proposal_for_task(task: AgentTask, proposal: ActionProposal) -> None:
    require_same_workspace(task, proposal)
    if proposal.task_id != task.task_id:
        raise RelationshipValidationError(
            "proposal.task_id does not match task.task_id"
        )


def validate_approval_for_proposal(
    proposal: ActionProposal, approval: ApprovalRequest
) -> None:
    require_same_workspace(proposal, approval)
    if approval.proposal_id != proposal.proposal_id:
        raise RelationshipValidationError(
            "approval.proposal_id does not match proposal.proposal_id"
        )
    if approval.task_id != proposal.task_id:
        raise RelationshipValidationError(
            "approval.task_id does not match proposal.task_id"
        )
    if approval.payload_hash != proposal.approval_payload_hash():
        raise RelationshipValidationError(
            "approval payload hash does not match the current proposal"
        )


def validate_event_for_task(task: AgentTask, event: TaskEvent) -> None:
    require_same_workspace(task, event)
    if event.task_id != task.task_id:
        raise RelationshipValidationError("event.task_id does not match task.task_id")


def validate_decision_context(
    decision: DecisionRecord,
    *,
    task: AgentTask | None = None,
    approval: ApprovalRequest | None = None,
) -> None:
    records: list[WorkspaceScoped] = [decision]
    if task is not None:
        records.append(task)
    if approval is not None:
        records.append(approval)
    require_same_workspace(*records)

    if task is not None and decision.related_task_id != task.task_id:
        raise RelationshipValidationError(
            "decision.related_task_id does not match task.task_id"
        )
    if approval is not None and decision.related_approval_id != approval.approval_id:
        raise RelationshipValidationError(
            "decision.related_approval_id does not match approval.approval_id"
        )
