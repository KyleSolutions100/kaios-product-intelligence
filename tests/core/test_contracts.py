from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from kaios.core.contracts import (
    DEFAULT_WORKSPACE_ID,
    ActionProposal,
    AgentResult,
    AgentTask,
    ApprovalRequest,
    ApprovalStatus,
    DecisionRecord,
    ResultStatus,
    RiskClassification,
    TaskEvent,
    Workspace,
    WorkspaceStatus,
    create_default_workspace,
)


def make_task(workspace_id: str = "workspace-a") -> AgentTask:
    return AgentTask(
        workspace_id=workspace_id,
        task_type="product_research",
        assigned_agent="product_intelligence",
    )


def test_default_workspace_is_generic_but_initialized_for_pod():
    workspace = create_default_workspace()

    assert workspace.workspace_id == DEFAULT_WORKSPACE_ID
    assert workspace.name == "Print-on-Demand Store"
    assert workspace.status is WorkspaceStatus.ACTIVE
    assert workspace.created_at.tzinfo is not None


def test_workspace_rejects_blank_identity_and_naive_timestamps():
    with pytest.raises(ValidationError):
        Workspace(workspace_id="", name="Example")

    with pytest.raises(ValidationError, match="timezone"):
        Workspace(
            workspace_id="example",
            name="Example",
            created_at=datetime.now(),
        )


def test_workspace_rejects_updated_at_before_created_at():
    now = datetime.now(timezone.utc)

    with pytest.raises(ValidationError, match="updated_at"):
        Workspace(
            workspace_id="example",
            name="Example",
            created_at=now,
            updated_at=now - timedelta(seconds=1),
        )


def test_contracts_forbid_unknown_fields():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AgentTask(
            workspace_id="workspace-a",
            task_type="research",
            assigned_agent="product_intelligence",
            unexpected=True,
        )


def test_task_cannot_be_its_own_parent():
    with pytest.raises(ValidationError, match="own parent"):
        AgentTask(
            task_id="task-1",
            parent_task_id="task-1",
            workspace_id="workspace-a",
            task_type="research",
            assigned_agent="product_intelligence",
        )


def test_task_mutable_defaults_are_not_shared():
    first = make_task()
    second = make_task()

    first.input_data["seed"] = "wedding invitations"

    assert second.input_data == {}


def test_factories_inherit_workspace_and_task_identity():
    task = make_task()
    proposal = ActionProposal.for_task(
        task,
        proposed_by_agent="store_operations",
        action_type="publish_listing",
        summary="Publish a draft listing",
        risk=RiskClassification.HIGH,
        is_public=True,
    )
    result = AgentResult.for_task(
        task,
        agent_id="product_intelligence",
        summary="Research completed",
        proposed_actions=[proposal],
    )
    approval = ApprovalRequest.for_proposal(proposal)
    decision = DecisionRecord.for_task(
        task,
        decision_type="opportunity_selection",
        summary="Selected a product opportunity",
        rationale="Best evidence-adjusted score",
    )

    assert proposal.workspace_id == task.workspace_id
    assert result.workspace_id == task.workspace_id
    assert approval.workspace_id == task.workspace_id
    assert decision.workspace_id == task.workspace_id
    assert proposal.task_id == task.task_id == result.task_id == approval.task_id


def test_failed_result_requires_error_and_success_rejects_error():
    task = make_task()

    with pytest.raises(ValidationError, match="failed results"):
        AgentResult.for_task(
            task,
            agent_id="product_intelligence",
            summary="Research failed",
            status=ResultStatus.FAILED,
        )

    with pytest.raises(ValidationError, match="only failed"):
        AgentResult.for_task(
            task,
            agent_id="product_intelligence",
            summary="Research completed",
            error="unexpected",
        )


def test_approval_hash_changes_when_exact_action_changes():
    task = make_task()
    first = ActionProposal.for_task(
        task,
        proposed_by_agent="store_operations",
        action_type="publish_listing",
        summary="Publish listing",
        payload={"title": "First title"},
    )
    changed = ActionProposal.for_task(
        task,
        proposed_by_agent="store_operations",
        action_type="publish_listing",
        summary="Publish listing",
        payload={"title": "Changed title"},
    )

    assert first.approval_payload_hash() != changed.approval_payload_hash()


def test_pending_approval_rejects_resolution_fields():
    task = make_task()
    proposal = ActionProposal.for_task(
        task,
        proposed_by_agent="marketing",
        action_type="publish_campaign",
        summary="Publish campaign",
    )

    with pytest.raises(ValidationError, match="pending approvals"):
        ApprovalRequest.for_proposal(
            proposal,
            resolved_at=datetime.now(timezone.utc),
            resolved_by="human_owner",
        )


def test_pending_approval_rejects_empty_resolution_reason():
    task = make_task()
    proposal = ActionProposal.for_task(
        task,
        proposed_by_agent="marketing",
        action_type="publish_campaign",
        summary="Publish campaign",
    )

    with pytest.raises(ValidationError, match="pending approvals"):
        ApprovalRequest.for_proposal(proposal, resolution_reason="")


def test_resolved_approval_requires_actor_and_timestamp():
    task = make_task()
    proposal = ActionProposal.for_task(
        task,
        proposed_by_agent="finance",
        action_type="approve_budget",
        summary="Approve budget",
    )

    with pytest.raises(ValidationError, match="resolved approvals"):
        ApprovalRequest.for_proposal(
            proposal,
            status=ApprovalStatus.APPROVED,
        )


def test_task_event_requires_complete_real_transition():
    task = make_task()

    with pytest.raises(ValidationError, match="set together"):
        TaskEvent(
            workspace_id=task.workspace_id,
            task_id=task.task_id,
            event_type="task_status_changed",
            from_status=task.status,
        )
