from __future__ import annotations

import pytest

from kaios.core.contracts import ActionProposal, AgentTask, Workspace
from kaios.core.workspaces import RelationshipValidationError
from kaios.repositories.interfaces import (
    ActionProposalRepository,
    DuplicateRecordError,
    RecordNotFoundError,
)
from kaios.repositories.memory import InMemoryRepositories
from kaios.repositories.sqlite import SQLiteRepositories


def build_repositories(kind: str, tmp_path):
    if kind == "memory":
        return InMemoryRepositories()
    return SQLiteRepositories(tmp_path / "kaios.db")


def add_task(repositories, workspace_id: str, task_id: str = "task-1") -> AgentTask:
    repositories.workspaces.add(
        Workspace(workspace_id=workspace_id, name=workspace_id)
    )
    return repositories.tasks.add(
        AgentTask(
            workspace_id=workspace_id,
            task_id=task_id,
            task_type="prepare_listing",
            assigned_agent="store_operations",
        )
    )


def proposal_for(task: AgentTask, proposal_id: str = "proposal-1") -> ActionProposal:
    return ActionProposal.for_task(
        task,
        proposal_id=proposal_id,
        proposed_by_agent=task.assigned_agent,
        action_type="publish_listing",
        summary="Publish the reviewed listing",
        payload={"listing": {"title": "Example"}},
        is_public=True,
    )


def test_proposal_repository_interface_is_abstract():
    with pytest.raises(TypeError):
        ActionProposalRepository()


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_proposals_are_workspace_scoped_and_append_only(kind, tmp_path):
    repositories = build_repositories(kind, tmp_path)
    pod_task = add_task(repositories, "pod", "shared-task")
    trading_task = add_task(repositories, "trading", "shared-task")
    pod = proposal_for(pod_task, "shared-proposal")
    trading = proposal_for(trading_task, "shared-proposal")

    repositories.proposals.add(pod)
    repositories.proposals.add(trading)

    assert repositories.proposals.get("pod", "shared-proposal") == pod
    assert repositories.proposals.get("trading", "shared-proposal") == trading
    assert repositories.proposals.list("pod", task_id="shared-task") == [pod]
    with pytest.raises(DuplicateRecordError):
        repositories.proposals.add(pod.model_copy(update={"summary": "Changed"}))


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_proposal_relationship_and_agent_identity_are_validated(kind, tmp_path):
    repositories = build_repositories(kind, tmp_path)
    task = add_task(repositories, "pod")
    missing_workspace_proposal = proposal_for(task).model_copy(
        update={"workspace_id": "trading"}
    )
    wrong_agent = proposal_for(task).model_copy(
        update={"proposed_by_agent": "marketing"}
    )

    with pytest.raises(RecordNotFoundError):
        repositories.proposals.add(missing_workspace_proposal)
    with pytest.raises(RelationshipValidationError, match="assigned_agent"):
        repositories.proposals.add(wrong_agent)


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_proposals_are_defensively_reconstructed(kind, tmp_path):
    repositories = build_repositories(kind, tmp_path)
    task = add_task(repositories, "pod")
    proposal = proposal_for(task)
    repositories.proposals.add(proposal)

    loaded = repositories.proposals.get("pod", proposal.proposal_id)
    loaded.payload["listing"]["title"] = "Mutated by caller"

    assert (
        repositories.proposals.get("pod", proposal.proposal_id).payload["listing"][
            "title"
        ]
        == "Example"
    )


def test_sqlite_proposals_survive_repository_restart(tmp_path):
    database_path = tmp_path / "kaios.db"
    first = SQLiteRepositories(database_path)
    task = add_task(first, "pod")
    proposal = first.proposals.add(proposal_for(task))

    restarted = SQLiteRepositories(database_path)

    assert restarted.proposals.get("pod", proposal.proposal_id) == proposal


def test_sqlite_version_one_database_migrates_to_proposals(tmp_path):
    database_path = tmp_path / "kaios.db"
    repositories = SQLiteRepositories(database_path)
    with repositories.database.transaction() as connection:
        connection.execute("DROP TABLE action_proposals")
        connection.execute("DELETE FROM schema_migrations WHERE version = 2")
        connection.execute("PRAGMA user_version = 1")

    restarted = SQLiteRepositories(database_path)
    task = add_task(restarted, "pod")
    proposal = proposal_for(task)

    assert restarted.proposals.add(proposal) == proposal
