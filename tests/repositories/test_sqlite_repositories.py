import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kaios.core.contracts import (
    ActionProposal,
    AgentResult,
    AgentTask,
    ApprovalRequest,
    ApprovalStatus,
    DecisionRecord,
    TaskEvent,
    TaskStatus,
    Workspace,
)
from kaios.core.lifecycle import InvalidTaskTransition, transition_task
from kaios.repositories.interfaces import (
    DuplicateRecordError,
    ImmutableRecordError,
    RecordNotFoundError,
)
from kaios.repositories.sqlite import (
    CURRENT_SCHEMA_VERSION,
    DEFAULT_DATABASE_PATH,
    SQLiteRepositories,
)


@pytest.fixture
def database_path(tmp_path):
    return tmp_path / "kaios.db"


@pytest.fixture
def repositories(database_path):
    return SQLiteRepositories(database_path)


def add_workspace(
    repositories: SQLiteRepositories, workspace_id: str
) -> Workspace:
    return repositories.workspaces.add(
        Workspace(workspace_id=workspace_id, name=f"Workspace {workspace_id}")
    )


def add_task(
    repositories: SQLiteRepositories,
    workspace_id: str,
    *,
    task_id: str | None = None,
    parent_task_id: str | None = None,
) -> AgentTask:
    values = {
        "workspace_id": workspace_id,
        "task_type": "product_research",
        "assigned_agent": "product_intelligence",
        "parent_task_id": parent_task_id,
    }
    if task_id is not None:
        values["task_id"] = task_id
    return repositories.tasks.add(AgentTask(**values))


def make_proposal(task: AgentTask) -> ActionProposal:
    return ActionProposal.for_task(
        task,
        proposed_by_agent="store_operations",
        action_type="publish_listing",
        summary="Publish listing",
        payload={"title": "Example"},
    )


def test_default_database_path_is_local_and_ignored_location():
    assert DEFAULT_DATABASE_PATH.as_posix() == "data/kaios.db"


def test_schema_version_metadata_foreign_keys_and_indexes(database_path):
    repositories = SQLiteRepositories(database_path)

    with repositories.database.read() as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        migrations = connection.execute(
            "SELECT version, applied_at FROM schema_migrations"
        ).fetchall()
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        indexes = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }

    assert version == CURRENT_SCHEMA_VERSION
    assert [row["version"] for row in migrations] == list(
        range(1, CURRENT_SCHEMA_VERSION + 1)
    )
    assert datetime.fromisoformat(migrations[0]["applied_at"]).tzinfo is not None
    assert foreign_keys == 1
    assert {
        "idx_tasks_status",
        "idx_results_task",
        "idx_approvals_status",
        "idx_decisions_created",
        "idx_events_task",
        "idx_proposals_task",
        "idx_proposals_action",
    } <= indexes


def test_records_persist_across_repository_instances(database_path):
    first = SQLiteRepositories(database_path)
    workspace = add_workspace(first, "pod")
    task = add_task(first, "pod")
    result = first.results.add(
        AgentResult.for_task(
            task,
            agent_id="product_intelligence",
            summary="Research complete",
        )
    )
    proposal = make_proposal(task)
    approval = first.approvals.add(
        ApprovalRequest.for_proposal(proposal), proposal=proposal
    )
    decision = first.decisions.add(
        DecisionRecord(
            workspace_id="pod",
            decision_type="review",
            summary="Await review",
            rationale="Human approval required",
            related_task_id=task.task_id,
            related_approval_id=approval.approval_id,
        )
    )
    event = first.events.add(
        TaskEvent(
            workspace_id="pod",
            task_id=task.task_id,
            event_type="task_created",
        )
    )

    restarted = SQLiteRepositories(database_path)

    assert restarted.workspaces.get("pod") == workspace
    assert restarted.tasks.get("pod", task.task_id) == task
    assert restarted.results.get("pod", result.result_id) == result
    assert restarted.approvals.get("pod", approval.approval_id) == approval
    assert restarted.decisions.get("pod", decision.decision_id) == decision
    assert restarted.events.get("pod", event.event_id) == event


def test_workspace_queries_are_isolated_even_when_ids_match(repositories):
    add_workspace(repositories, "pod")
    add_workspace(repositories, "trading")
    pod_task = add_task(repositories, "pod", task_id="shared")
    trading_task = add_task(repositories, "trading", task_id="shared")
    pod_result = AgentResult.for_task(
        pod_task,
        result_id="shared-result",
        agent_id="product_intelligence",
        summary="POD result",
    )
    trading_result = AgentResult.for_task(
        trading_task,
        result_id="shared-result",
        agent_id="trading_intelligence",
        summary="Trading result",
    )
    repositories.results.add(pod_result)
    repositories.results.add(trading_result)
    pod_proposal = make_proposal(pod_task).model_copy(
        update={"proposal_id": "shared-proposal"}
    )
    trading_proposal = make_proposal(trading_task).model_copy(
        update={"proposal_id": "shared-proposal"}
    )
    pod_approval = ApprovalRequest.for_proposal(
        pod_proposal, approval_id="shared-approval"
    )
    trading_approval = ApprovalRequest.for_proposal(
        trading_proposal, approval_id="shared-approval"
    )
    repositories.approvals.add(pod_approval, proposal=pod_proposal)
    repositories.approvals.add(trading_approval, proposal=trading_proposal)
    pod_decision = DecisionRecord.for_task(
        pod_task,
        decision_id="shared-decision",
        decision_type="select_product",
        summary="POD decision",
        rationale="POD evidence",
    )
    trading_decision = DecisionRecord.for_task(
        trading_task,
        decision_id="shared-decision",
        decision_type="select_asset",
        summary="Trading decision",
        rationale="Trading evidence",
    )
    repositories.decisions.add(pod_decision)
    repositories.decisions.add(trading_decision)
    pod_event = TaskEvent(
        event_id="shared-event",
        workspace_id="pod",
        task_id="shared",
        event_type="pod_event",
    )
    trading_event = TaskEvent(
        event_id="shared-event",
        workspace_id="trading",
        task_id="shared",
        event_type="trading_event",
    )
    repositories.events.add(pod_event)
    repositories.events.add(trading_event)

    assert repositories.tasks.get("pod", "shared") == pod_task
    assert repositories.tasks.get("trading", "shared") == trading_task
    assert repositories.results.get("pod", "shared-result") == pod_result
    assert repositories.results.get("trading", "shared-result") == trading_result
    assert repositories.results.list_for_task("pod", "shared") == [pod_result]
    assert repositories.results.list_for_task("trading", "shared") == [
        trading_result
    ]
    assert repositories.approvals.get("pod", "shared-approval") == pod_approval
    assert (
        repositories.approvals.get("trading", "shared-approval")
        == trading_approval
    )
    assert repositories.approvals.list("pod") == [pod_approval]
    assert repositories.approvals.list("trading") == [trading_approval]
    assert repositories.decisions.get("pod", "shared-decision") == pod_decision
    assert (
        repositories.decisions.get("trading", "shared-decision")
        == trading_decision
    )
    assert repositories.decisions.list("pod") == [pod_decision]
    assert repositories.decisions.list("trading") == [trading_decision]
    assert repositories.events.get("pod", "shared-event") == pod_event
    assert repositories.events.get("trading", "shared-event") == trading_event
    assert repositories.events.list_for_task("pod", "shared") == [pod_event]
    assert repositories.events.list_for_task("trading", "shared") == [trading_event]


def test_cross_workspace_relationships_are_rejected(repositories):
    add_workspace(repositories, "pod")
    add_workspace(repositories, "trading")
    task = add_task(repositories, "pod")

    with pytest.raises(RecordNotFoundError, match="parent task"):
        add_task(repositories, "trading", parent_task_id=task.task_id)

    wrong_workspace_result = AgentResult(
        workspace_id="trading",
        task_id=task.task_id,
        agent_id="product_intelligence",
        summary="Wrong workspace",
    )
    with pytest.raises(RecordNotFoundError, match="result task"):
        repositories.results.add(wrong_workspace_result)


def test_sqlite_foreign_keys_reject_invalid_relationships(repositories):
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        with repositories.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO tasks(
                    workspace_id, task_id, parent_task_id, status,
                    created_at, updated_at, payload_json
                ) VALUES (?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    "missing",
                    "orphan",
                    TaskStatus.CREATED.value,
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:00:00+00:00",
                    "{}",
                ),
            )


def test_task_lifecycle_and_parent_immutability_are_enforced(repositories):
    add_workspace(repositories, "pod")
    parent = add_task(repositories, "pod")
    task = add_task(repositories, "pod", parent_task_id=parent.task_id)
    queued, _ = transition_task(task, TaskStatus.QUEUED)

    repositories.tasks.update(queued)

    assert repositories.tasks.list("pod", status=TaskStatus.QUEUED) == [queued]
    illegal = queued.model_copy(update={"status": TaskStatus.SUCCEEDED})
    with pytest.raises(InvalidTaskTransition):
        repositories.tasks.update(illegal)
    changed_parent = queued.model_copy(update={"parent_task_id": None})
    with pytest.raises(ImmutableRecordError, match="parent"):
        repositories.tasks.update(changed_parent)


def test_approval_can_only_be_resolved_once(repositories):
    add_workspace(repositories, "pod")
    task = add_task(repositories, "pod")
    proposal = make_proposal(task)
    pending = ApprovalRequest.for_proposal(proposal)
    repositories.approvals.add(pending, proposal=proposal)
    approved = pending.model_copy(
        update={
            "status": ApprovalStatus.APPROVED,
            "resolved_at": datetime.now(timezone.utc),
            "resolved_by": "human_owner",
        }
    )
    repositories.approvals.update(approved)
    second_resolution = approved.model_copy(
        update={
            "status": ApprovalStatus.REJECTED,
            "resolution_reason": "Changed mind",
        }
    )

    with pytest.raises(ImmutableRecordError, match="resolved approval"):
        repositories.approvals.update(second_resolution)

    assert repositories.approvals.get("pod", pending.approval_id) == approved


def test_approval_identity_cannot_change(repositories):
    add_workspace(repositories, "pod")
    task = add_task(repositories, "pod")
    proposal = make_proposal(task)
    pending = ApprovalRequest.for_proposal(proposal)
    repositories.approvals.add(pending, proposal=proposal)

    changed = pending.model_copy(update={"payload_hash": "0" * 64})
    with pytest.raises(ImmutableRecordError, match="payload hash"):
        repositories.approvals.update(changed)


def test_results_decisions_and_events_are_append_only(repositories):
    add_workspace(repositories, "pod")
    task = add_task(repositories, "pod")
    result = AgentResult.for_task(
        task,
        agent_id="product_intelligence",
        summary="Research complete",
    )
    decision = DecisionRecord.for_task(
        task,
        decision_type="select_product",
        summary="Selected product",
        rationale="Best score",
    )
    event = TaskEvent(
        workspace_id="pod",
        task_id=task.task_id,
        event_type="task_created",
    )
    repositories.results.add(result)
    repositories.decisions.add(decision)
    repositories.events.add(event)

    changed_result = result.model_copy(update={"summary": "Overwrite attempt"})
    changed_decision = decision.model_copy(update={"summary": "Overwrite attempt"})
    changed_event = event.model_copy(update={"event_type": "overwrite_attempt"})

    for add, changed in (
        (repositories.results.add, changed_result),
        (repositories.decisions.add, changed_decision),
        (repositories.events.add, changed_event),
    ):
        with pytest.raises(DuplicateRecordError):
            add(changed)

    assert repositories.results.get("pod", result.result_id) == result
    assert repositories.decisions.get("pod", decision.decision_id) == decision
    assert repositories.events.get("pod", event.event_id) == event


def test_structured_json_and_timezone_aware_timestamps_round_trip(repositories):
    offset = timezone(timedelta(hours=5, minutes=30))
    moment = datetime(2026, 8, 6, 12, 34, 56, 123456, tzinfo=offset)
    workspace = Workspace(
        workspace_id="pod",
        name="POD",
        created_at=moment,
        updated_at=moment,
    )
    repositories.workspaces.add(workspace)
    task = AgentTask(
        task_id="complex-task",
        workspace_id="pod",
        task_type="product_research",
        assigned_agent="product_intelligence",
        input_data={"nested": {"values": [1, True, None, "£"]}},
        created_at=moment,
        updated_at=moment,
    )
    repositories.tasks.add(task)
    proposal = ActionProposal.for_task(
        task,
        proposal_id="proposal-complex",
        proposed_by_agent="store_operations",
        action_type="publish_listing",
        summary="Publish listing",
        payload={"price": "19.95", "tags": ["gift", "custom"]},
        estimated_cost=Decimal("4.25"),
        currency="GBP",
        created_at=moment,
    )
    result = AgentResult.for_task(
        task,
        result_id="complex-result",
        agent_id="product_intelligence",
        summary="Complete",
        data={"scores": {"demand": 8.75}},
        evidence=[{"url": "https://example.invalid", "rank": 1}],
        recommendations=[{"action": "test", "confidence": "high"}],
        proposed_actions=[proposal],
        created_at=moment,
    )
    repositories.results.add(result)

    stored_task = repositories.tasks.get("pod", task.task_id)
    stored_result = repositories.results.get("pod", result.result_id)

    assert stored_task == task
    assert stored_result == result
    assert stored_task.created_at.tzinfo is not None
    assert stored_result.proposed_actions[0].estimated_cost == Decimal("4.25")
    assert stored_result.data == {"scores": {"demand": 8.75}}


def test_reconstructed_records_do_not_share_nested_mutable_data(repositories):
    add_workspace(repositories, "pod")
    task = add_task(repositories, "pod")
    result = AgentResult.for_task(
        task,
        agent_id="product_intelligence",
        summary="Complete",
        data={"nested": {"score": 1}},
    )
    repositories.results.add(result)

    fetched = repositories.results.get("pod", result.result_id)
    fetched.data["nested"]["score"] = 99

    assert repositories.results.get("pod", result.result_id).data == {
        "nested": {"score": 1}
    }


def test_transaction_rolls_back_all_writes_after_failure(repositories):
    workspace = Workspace(workspace_id="rolled-back", name="Rolled back")
    row = (
        workspace.workspace_id,
        workspace.status.value,
        workspace.created_at.isoformat(),
        workspace.updated_at.isoformat(),
        workspace.model_dump_json(),
    )

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        with repositories.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO workspaces(
                    workspace_id, status, created_at, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                row,
            )
            connection.execute(
                """
                INSERT INTO workspaces(
                    workspace_id, status, created_at, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                row,
            )

    assert repositories.workspaces.get("rolled-back") is None
