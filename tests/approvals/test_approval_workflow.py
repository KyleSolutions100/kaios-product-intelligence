from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kaios.approvals import (
    ActionApprovalService,
    ApprovalActorError,
    ApprovalExpiredError,
    ApprovalStateError,
    ExecutionBlockedError,
    PolicyConfig,
    PolicyEngine,
    PolicyOutcome,
    ProposalMismatchError,
)
from kaios.core.contracts import (
    ActionProposal,
    AgentTask,
    ApprovalStatus,
    RiskClassification,
    TaskStatus,
    Workspace,
)
from kaios.core.lifecycle import transition_task
from kaios.repositories.interfaces import RecordNotFoundError
from kaios.repositories.memory import InMemoryRepositories
from kaios.repositories.sqlite import SQLiteRepositories


BASE_TIME = datetime(2026, 8, 6, 9, 0, tzinfo=timezone.utc)


def build_repositories(kind: str, tmp_path):
    if kind == "memory":
        return InMemoryRepositories()
    return SQLiteRepositories(tmp_path / "kaios.db")


def running_task(repositories, workspace_id: str = "pod") -> AgentTask:
    repositories.workspaces.add(
        Workspace(
            workspace_id=workspace_id,
            name=workspace_id,
            created_at=BASE_TIME,
            updated_at=BASE_TIME,
        )
    )
    task = repositories.tasks.add(
        AgentTask(
            task_id=f"task-{workspace_id}",
            workspace_id=workspace_id,
            task_type="prepare_listing",
            assigned_agent="store_operations",
            created_at=BASE_TIME,
            updated_at=BASE_TIME,
        )
    )
    for index, status in enumerate((TaskStatus.QUEUED, TaskStatus.RUNNING), start=1):
        task, event = transition_task(
            task, status, occurred_at=BASE_TIME + timedelta(minutes=index)
        )
        repositories.tasks.update(task)
        repositories.events.add(event)
    return task


def risky_proposal(task: AgentTask, **updates) -> ActionProposal:
    values = {
        "proposal_id": f"proposal-{task.workspace_id}",
        "proposed_by_agent": task.assigned_agent,
        "action_type": "publish_listing",
        "summary": "Publish listing to Etsy",
        "payload": {"listing_id": "draft-1", "title": "Example"},
        "risk": RiskClassification.HIGH,
        "is_public": True,
        "created_at": BASE_TIME + timedelta(minutes=3),
    }
    values.update(updates)
    return ActionProposal.for_task(task, **values)


def service_for(repositories, *, now=BASE_TIME + timedelta(minutes=4), **kwargs):
    return ActionApprovalService(repositories, now=lambda: now, **kwargs)


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_risky_proposal_is_stored_and_waits_for_exact_human_approval(kind, tmp_path):
    repositories = build_repositories(kind, tmp_path)
    task = running_task(repositories)
    proposal = risky_proposal(task)
    service = service_for(repositories)

    review = service.create_proposal(proposal)

    assert review.policy.outcome is PolicyOutcome.APPROVAL_REQUIRED
    assert review.approval.payload_hash == proposal.approval_payload_hash()
    assert repositories.proposals.get("pod", proposal.proposal_id) == proposal
    assert repositories.tasks.get("pod", task.task_id).status is TaskStatus.WAITING_FOR_APPROVAL
    assert service.list_pending_approvals("pod")[0].proposal == proposal
    event_types = [
        event.event_type
        for event in repositories.events.list_for_task("pod", task.task_id)
    ]
    assert {
        "proposal_created",
        "policy_evaluated",
        "approval_requested",
        "task_status_changed",
    } <= set(event_types)


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_risky_action_cannot_execute_until_human_approval(kind, tmp_path):
    repositories = build_repositories(kind, tmp_path)
    task = running_task(repositories)
    proposal = risky_proposal(task)
    service = service_for(repositories)
    review = service.create_proposal(proposal)

    with pytest.raises(ExecutionBlockedError, match="without approval"):
        service.simulate_execution(proposal)

    approved = service.approve(
        "pod", review.approval.approval_id, actor_id="human_owner"
    )
    execution = service.simulate_execution(
        proposal, approval_id=approved.approval_id
    )

    assert approved.status is ApprovalStatus.APPROVED
    assert repositories.tasks.get("pod", task.task_id).status is TaskStatus.QUEUED
    assert execution.status == "simulated"
    assert execution.note == "Simulation only; no external action was performed."
    event_types = [
        event.event_type
        for event in repositories.events.list_for_task("pod", task.task_id)
    ]
    assert "execution_blocked" in event_types
    assert "approval_approved" in event_types
    assert "simulated_execution_completed" in event_types


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_changed_payload_invalidates_approval(kind, tmp_path):
    repositories = build_repositories(kind, tmp_path)
    proposal = risky_proposal(running_task(repositories))
    service = service_for(repositories)
    review = service.create_proposal(proposal)
    service.approve("pod", review.approval.approval_id, actor_id="human_owner")
    changed = ActionProposal.model_validate(
        {
            **proposal.model_dump(),
            "payload": {**proposal.payload, "title": "Changed after approval"},
        }
    )

    with pytest.raises(ProposalMismatchError):
        service.simulate_execution(changed, approval_id=review.approval.approval_id)


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_ceo_and_nonhuman_actors_cannot_resolve_approval(kind, tmp_path):
    repositories = build_repositories(kind, tmp_path)
    proposal = risky_proposal(running_task(repositories))
    service = service_for(repositories)
    approval = service.create_proposal(proposal).approval

    with pytest.raises(ApprovalActorError, match="CEO orchestrator"):
        service.approve("pod", approval.approval_id, actor_id="ceo_orchestrator")
    with pytest.raises(ApprovalActorError, match="explicitly configured human"):
        service.reject("pod", approval.approval_id, actor_id="product_intelligence")
    assert repositories.approvals.get("pod", approval.approval_id).status is ApprovalStatus.PENDING


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_cross_workspace_and_second_resolution_are_rejected(kind, tmp_path):
    repositories = build_repositories(kind, tmp_path)
    proposal = risky_proposal(running_task(repositories, "pod"))
    running_task(repositories, "trading")
    service = service_for(repositories)
    approval = service.create_proposal(proposal).approval

    with pytest.raises(RecordNotFoundError):
        service.approve("trading", approval.approval_id, actor_id="human_owner")
    service.approve("pod", approval.approval_id, actor_id="human_owner")
    with pytest.raises(ApprovalStateError, match="already"):
        service.reject("pod", approval.approval_id, actor_id="human_owner")


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_rejection_cancels_task_and_cannot_be_reused(kind, tmp_path):
    repositories = build_repositories(kind, tmp_path)
    task = running_task(repositories)
    proposal = risky_proposal(task)
    service = service_for(repositories)
    approval = service.create_proposal(proposal).approval

    rejected = service.reject(
        "pod", approval.approval_id, actor_id="human_owner", reason="Not approved"
    )

    assert rejected.status is ApprovalStatus.REJECTED
    assert repositories.tasks.get("pod", task.task_id).status is TaskStatus.CANCELLED
    with pytest.raises(ExecutionBlockedError, match="only an approved"):
        service.simulate_execution(proposal, approval_id=approval.approval_id)


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_expiration_cancels_task_and_expired_approval_cannot_execute(kind, tmp_path):
    repositories = build_repositories(kind, tmp_path)
    task = running_task(repositories)
    proposal = risky_proposal(task)
    service = service_for(repositories, approval_ttl=timedelta(minutes=10))
    approval = service.create_proposal(proposal).approval
    expiry_time = approval.requested_at + timedelta(minutes=10)

    expired = service.expire("pod", approval.approval_id, at=expiry_time)

    assert expired.status is ApprovalStatus.EXPIRED
    assert repositories.tasks.get("pod", task.task_id).status is TaskStatus.CANCELLED
    with pytest.raises(ExecutionBlockedError, match="only an approved"):
        service.simulate_execution(
            proposal, approval_id=approval.approval_id, at=expiry_time
        )


def test_approved_request_also_has_a_bounded_validity_period(tmp_path):
    repositories = InMemoryRepositories()
    proposal = risky_proposal(running_task(repositories))
    service = service_for(repositories, approval_ttl=timedelta(minutes=10))
    approval = service.create_proposal(proposal).approval
    service.approve("pod", approval.approval_id, actor_id="human_owner")

    with pytest.raises(ApprovalExpiredError):
        service.simulate_execution(
            proposal,
            approval_id=approval.approval_id,
            at=approval.requested_at + timedelta(minutes=10),
        )


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_allowlisted_read_only_action_needs_no_approval(kind, tmp_path):
    repositories = build_repositories(kind, tmp_path)
    task = running_task(repositories)
    proposal = risky_proposal(
        task,
        action_type="read_marketplace_data",
        summary="Read cached marketplace evidence",
        risk=RiskClassification.LOW,
        is_public=False,
    )
    service = service_for(repositories)

    review = service.create_proposal(proposal)
    execution = service.simulate_execution(proposal)

    assert review.policy.outcome is PolicyOutcome.APPROVAL_NOT_REQUIRED
    assert review.approval is None
    assert repositories.tasks.get("pod", task.task_id).status is TaskStatus.RUNNING
    assert execution.approval_id is None


@pytest.mark.parametrize(
    ("updates", "minimum_risk"),
    [
        ({"action_type": "unrecognized_action", "risk": RiskClassification.LOW, "is_public": False}, RiskClassification.MEDIUM),
        ({"action_type": "send_external_message", "is_public": False}, RiskClassification.HIGH),
        ({"action_type": "financial_transfer", "estimated_cost": Decimal("10")}, RiskClassification.HIGH),
        ({"action_type": "delete_record", "is_reversible": False}, RiskClassification.CRITICAL),
        ({"action_type": "use_paid_ai", "estimated_cost": Decimal("1"), "is_public": False}, RiskClassification.HIGH),
    ],
)
def test_default_deny_policy_classifies_unknown_and_risky_actions(updates, minimum_risk):
    repositories = InMemoryRepositories()
    task = running_task(repositories)
    proposal = risky_proposal(task, **updates)

    decision = PolicyEngine(
        PolicyConfig(paid_ai_approval_threshold=Decimal("0.50"))
    ).evaluate(proposal)

    assert decision.outcome is PolicyOutcome.APPROVAL_REQUIRED
    order = list(RiskClassification)
    assert order.index(decision.risk) >= order.index(minimum_risk)


def test_configured_block_is_a_hard_stop_with_audit_event(tmp_path):
    repositories = InMemoryRepositories()
    task = running_task(repositories)
    proposal = risky_proposal(task, action_type="delete_everything")
    service = service_for(
        repositories,
        policy_engine=PolicyEngine(
            PolicyConfig(blocked_action_types=frozenset({"delete_everything"}))
        ),
    )
    review = service.create_proposal(proposal)

    assert review.policy.outcome is PolicyOutcome.BLOCKED
    with pytest.raises(ExecutionBlockedError, match="blocked"):
        service.simulate_execution(proposal)
    assert "execution_blocked" in {
        event.event_type
        for event in repositories.events.list_for_task("pod", task.task_id)
    }


def test_sqlite_approval_records_survive_restart(tmp_path):
    database_path = tmp_path / "kaios.db"
    repositories = SQLiteRepositories(database_path)
    task = running_task(repositories)
    proposal = risky_proposal(task)
    approval = service_for(repositories).create_proposal(proposal).approval

    restarted = SQLiteRepositories(database_path)
    pending = service_for(restarted).list_pending_approvals("pod")

    assert restarted.proposals.get("pod", proposal.proposal_id) == proposal
    assert pending[0].approval == approval
    assert pending[0].proposal == proposal
