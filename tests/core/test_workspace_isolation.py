import pytest

from kaios.core.contracts import (
    ActionProposal,
    AgentResult,
    AgentTask,
    ApprovalRequest,
    DecisionRecord,
    TaskEvent,
)
from kaios.core.workspaces import (
    RelationshipValidationError,
    WorkspaceBoundaryError,
    records_for_workspace,
    require_same_workspace,
    validate_approval_for_proposal,
    validate_decision_context,
    validate_event_for_task,
    validate_result_for_task,
    validate_task_relationship,
)


def make_task(workspace_id: str, **values) -> AgentTask:
    return AgentTask(
        workspace_id=workspace_id,
        task_type="product_research",
        assigned_agent="product_intelligence",
        **values,
    )


def make_proposal(task: AgentTask) -> ActionProposal:
    return ActionProposal.for_task(
        task,
        proposed_by_agent="store_operations",
        action_type="publish_listing",
        summary="Publish listing",
    )


def test_workspace_query_never_returns_records_from_another_workspace():
    pod_task = make_task("pod")
    trading_task = make_task("trading")

    selected = records_for_workspace([pod_task, trading_task], "pod")

    assert selected == [pod_task]
    assert all(record.workspace_id == "pod" for record in selected)


def test_require_same_workspace_rejects_mixed_records():
    with pytest.raises(WorkspaceBoundaryError, match="cross-workspace"):
        require_same_workspace(make_task("pod"), make_task("trading"))


def test_result_cannot_attach_to_task_in_another_workspace():
    pod_task = make_task("pod")
    result = AgentResult(
        workspace_id="trading",
        task_id=pod_task.task_id,
        agent_id="product_intelligence",
        summary="Incorrectly scoped result",
    )

    with pytest.raises(WorkspaceBoundaryError):
        validate_result_for_task(pod_task, result)


def test_result_task_id_must_match_even_inside_workspace():
    task = make_task("pod")
    another_task = make_task("pod")
    result = AgentResult.for_task(
        another_task,
        agent_id="product_intelligence",
        summary="Wrong task result",
    )

    with pytest.raises(RelationshipValidationError, match="result.task_id"):
        validate_result_for_task(task, result)


def test_approval_cannot_attach_to_proposal_in_another_workspace():
    pod_proposal = make_proposal(make_task("pod"))
    trading_proposal = make_proposal(make_task("trading"))
    trading_approval = ApprovalRequest.for_proposal(trading_proposal)

    with pytest.raises(WorkspaceBoundaryError):
        validate_approval_for_proposal(pod_proposal, trading_approval)


def test_approval_is_bound_to_exact_proposal_payload():
    task = make_task("pod")
    proposal = make_proposal(task)
    approval = ApprovalRequest.for_proposal(proposal)
    changed = ActionProposal(
        **proposal.model_dump(exclude={"payload"}),
        payload={"title": "Changed after approval"},
    )

    with pytest.raises(RelationshipValidationError, match="payload hash"):
        validate_approval_for_proposal(changed, approval)


def test_decision_and_approval_remain_isolated_by_workspace():
    pod_task = make_task("pod")
    trading_task = make_task("trading")
    trading_approval = ApprovalRequest.for_proposal(make_proposal(trading_task))
    decision = DecisionRecord.for_task(
        pod_task,
        decision_type="select_product",
        summary="Select product",
        rationale="Best score",
        related_approval_id=trading_approval.approval_id,
    )

    with pytest.raises(WorkspaceBoundaryError):
        validate_decision_context(
            decision,
            task=pod_task,
            approval=trading_approval,
        )


def test_orchestrator_relationship_rejects_cross_workspace_child_task():
    parent = make_task("pod")
    child = make_task("trading", parent_task_id=parent.task_id)

    with pytest.raises(WorkspaceBoundaryError):
        validate_task_relationship(parent, child)


def test_child_task_must_reference_supplied_parent():
    parent = make_task("pod")
    child = make_task("pod", parent_task_id="different-parent")

    with pytest.raises(RelationshipValidationError, match="parent_task_id"):
        validate_task_relationship(parent, child)


def test_task_event_cannot_cross_workspace_boundary():
    task = make_task("pod")
    event = TaskEvent(
        workspace_id="trading",
        task_id=task.task_id,
        event_type="note_added",
    )

    with pytest.raises(WorkspaceBoundaryError):
        validate_event_for_task(task, event)
