from datetime import datetime, timezone

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
from kaios.core.workspaces import WorkspaceBoundaryError
from kaios.repositories.interfaces import (
    ApprovalRepository,
    DecisionRepository,
    DuplicateRecordError,
    EventRepository,
    ImmutableRecordError,
    RecordNotFoundError,
    ResultRepository,
    TaskRepository,
    WorkspaceRepository,
)
from kaios.repositories.memory import InMemoryRepositories


def add_workspace(repositories: InMemoryRepositories, workspace_id: str) -> Workspace:
    return repositories.workspaces.add(
        Workspace(workspace_id=workspace_id, name=f"Workspace {workspace_id}")
    )


def add_task(
    repositories: InMemoryRepositories,
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


@pytest.mark.parametrize(
    "repository_type",
    [
        WorkspaceRepository,
        TaskRepository,
        ResultRepository,
        ApprovalRepository,
        DecisionRepository,
        EventRepository,
    ],
)
def test_repository_interfaces_are_abstract(repository_type):
    with pytest.raises(TypeError):
        repository_type()


def test_workspace_repository_add_get_list_and_update():
    repositories = InMemoryRepositories()
    pod = add_workspace(repositories, "pod")
    trading = add_workspace(repositories, "trading")

    updated = Workspace(
        **pod.model_dump(exclude={"description", "updated_at"}),
        description="Updated description",
        updated_at=datetime.now(timezone.utc),
    )
    repositories.workspaces.update(updated)

    assert repositories.workspaces.get("pod") == updated
    assert repositories.workspaces.list() == [updated, trading]


def test_duplicate_workspace_is_rejected():
    repositories = InMemoryRepositories()
    workspace = add_workspace(repositories, "pod")

    with pytest.raises(DuplicateRecordError):
        repositories.workspaces.add(workspace)


def test_task_requires_existing_workspace():
    repositories = InMemoryRepositories()

    with pytest.raises(RecordNotFoundError, match="workspace"):
        add_task(repositories, "missing")


def test_task_queries_are_isolated_by_workspace_even_with_same_task_id():
    repositories = InMemoryRepositories()
    add_workspace(repositories, "pod")
    add_workspace(repositories, "trading")
    pod_task = add_task(repositories, "pod", task_id="shared-id")
    trading_task = add_task(repositories, "trading", task_id="shared-id")

    assert repositories.tasks.get("pod", "shared-id") == pod_task
    assert repositories.tasks.get("trading", "shared-id") == trading_task
    assert repositories.tasks.list("pod") == [pod_task]
    assert repositories.tasks.list("trading") == [trading_task]


def test_cross_workspace_parent_relationship_is_rejected():
    repositories = InMemoryRepositories()
    add_workspace(repositories, "pod")
    add_workspace(repositories, "trading")
    parent = add_task(repositories, "pod")

    with pytest.raises(RecordNotFoundError, match="parent task"):
        add_task(repositories, "trading", parent_task_id=parent.task_id)


def test_task_update_enforces_lifecycle_and_status_filter():
    repositories = InMemoryRepositories()
    add_workspace(repositories, "pod")
    task = add_task(repositories, "pod")
    queued, _ = transition_task(task, TaskStatus.QUEUED)

    repositories.tasks.update(queued)

    assert repositories.tasks.list("pod", status=TaskStatus.QUEUED) == [queued]
    illegal = queued.model_copy(update={"status": TaskStatus.SUCCEEDED})
    with pytest.raises(InvalidTaskTransition):
        repositories.tasks.update(illegal)


def test_result_repository_validates_task_and_workspace():
    repositories = InMemoryRepositories()
    add_workspace(repositories, "pod")
    add_workspace(repositories, "trading")
    task = add_task(repositories, "pod")
    result = AgentResult.for_task(
        task,
        agent_id="product_intelligence",
        summary="Research complete",
        data={"opportunities": ["example"]},
    )

    repositories.results.add(result)

    assert repositories.results.get("pod", result.result_id) == result
    assert repositories.results.get("trading", result.result_id) is None
    assert repositories.results.list_for_task("pod", task.task_id) == [result]
    wrong_workspace = result.model_copy(
        update={"result_id": "wrong-workspace", "workspace_id": "trading"}
    )
    with pytest.raises(RecordNotFoundError, match="result task"):
        repositories.results.add(wrong_workspace)


def test_repository_returns_deep_copies_of_nested_data():
    repositories = InMemoryRepositories()
    add_workspace(repositories, "pod")
    task = add_task(repositories, "pod")
    result = AgentResult.for_task(
        task,
        agent_id="product_intelligence",
        summary="Research complete",
        data={"nested": {"score": 1}},
    )
    repositories.results.add(result)

    fetched = repositories.results.get("pod", result.result_id)
    fetched.data["nested"]["score"] = 99

    assert repositories.results.get("pod", result.result_id).data == {
        "nested": {"score": 1}
    }


def test_approval_repository_validates_exact_proposal_and_isolates_queries():
    repositories = InMemoryRepositories()
    add_workspace(repositories, "pod")
    add_workspace(repositories, "trading")
    pod_task = add_task(repositories, "pod")
    trading_task = add_task(repositories, "trading")
    pod_proposal = make_proposal(pod_task)
    trading_proposal = make_proposal(trading_task)
    approval = ApprovalRequest.for_proposal(pod_proposal)

    repositories.approvals.add(approval, proposal=pod_proposal)

    assert repositories.approvals.list("pod") == [approval]
    assert repositories.approvals.list("trading") == []
    with pytest.raises(WorkspaceBoundaryError):
        repositories.approvals.add(approval, proposal=trading_proposal)


def test_approval_can_be_resolved_once_without_changing_identity():
    repositories = InMemoryRepositories()
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

    assert repositories.approvals.get("pod", approved.approval_id) == approved
    changed = approved.model_copy(update={"payload_hash": "0" * 64})
    with pytest.raises(ImmutableRecordError):
        repositories.approvals.update(changed)


def test_decision_repository_validates_and_isolates_relationships():
    repositories = InMemoryRepositories()
    add_workspace(repositories, "pod")
    add_workspace(repositories, "trading")
    task = add_task(repositories, "pod")
    decision = DecisionRecord.for_task(
        task,
        decision_type="select_product",
        summary="Selected product",
        rationale="Best score",
    )

    repositories.decisions.add(decision)

    assert repositories.decisions.list("pod") == [decision]
    assert repositories.decisions.list("trading") == []
    wrong_workspace = decision.model_copy(
        update={"decision_id": "wrong-workspace", "workspace_id": "trading"}
    )
    with pytest.raises(RecordNotFoundError, match="decision task"):
        repositories.decisions.add(wrong_workspace)


def test_event_repository_is_append_only_and_workspace_scoped():
    repositories = InMemoryRepositories()
    add_workspace(repositories, "pod")
    add_workspace(repositories, "trading")
    task = add_task(repositories, "pod")
    event = TaskEvent(
        workspace_id="pod",
        task_id=task.task_id,
        event_type="task_created",
    )

    repositories.events.add(event)

    assert repositories.events.get("pod", event.event_id) == event
    assert repositories.events.get("trading", event.event_id) is None
    assert repositories.events.list_for_task("pod", task.task_id) == [event]
    assert repositories.events.list_for_task("trading", task.task_id) == []
    with pytest.raises(DuplicateRecordError):
        repositories.events.add(event)
