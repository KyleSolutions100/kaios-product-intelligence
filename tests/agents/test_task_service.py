from pathlib import Path

import pytest

from kaios.agents import (
    PRODUCT_INTELLIGENCE_AGENT_ID,
    PRODUCT_RESEARCH_TASK_TYPE,
    AgentRegistry,
    AgentTaskService,
    ProductIntelligenceAgent,
    UnsupportedTaskTypeError,
)
from kaios.core.contracts import AgentTask, ResultStatus, TaskStatus, Workspace
from kaios.repositories.interfaces import RecordNotFoundError
from kaios.repositories.memory import InMemoryRepositories
from kaios.repositories.sqlite import SQLiteRepositories


EVIDENCE = [
    {
        "title": "Popular Eco Invitation",
        "url": "https://example.invalid/listing",
        "content": "Popular recycled product with many reviews " * 5,
        "source": "Mock marketplace",
        "source_type": "mock",
    }
]


class StaticExtractor:
    def gather(self, seed):
        return EVIDENCE


class EmptyExtractor:
    def gather(self, seed):
        return []


def build_runtime(repository_kind, tmp_path, extractor_factory=None):
    if repository_kind == "memory":
        repositories = InMemoryRepositories()
    else:
        repositories = SQLiteRepositories(tmp_path / "kaios.db")
    repositories.workspaces.add(Workspace(workspace_id="pod", name="POD"))
    repositories.workspaces.add(
        Workspace(workspace_id="trading", name="Trading")
    )
    agent = ProductIntelligenceAgent(
        extractor_factory=extractor_factory
        or (lambda **kwargs: StaticExtractor())
    )
    registry = AgentRegistry()
    registry.register(agent)
    return repositories, AgentTaskService(registry, repositories)


def add_research_task(repositories, tmp_path, **updates):
    values = {
        "workspace_id": "pod",
        "task_type": PRODUCT_RESEARCH_TASK_TYPE,
        "assigned_agent": PRODUCT_INTELLIGENCE_AGENT_ID,
        "input_data": {
            "seed": "eco invitation",
            "marketplace": "etsy",
            "result_limit": 5,
            "report_output_location": str(tmp_path / "reports"),
        },
    }
    values.update(updates)
    return repositories.tasks.add(AgentTask(**values))


@pytest.mark.parametrize("repository_kind", ["memory", "sqlite"])
def test_task_service_persists_result_lifecycle_and_events(
    repository_kind, tmp_path
):
    repositories, service = build_runtime(repository_kind, tmp_path)
    task = add_research_task(repositories, tmp_path)

    result = service.execute("pod", task.task_id)

    stored_task = repositories.tasks.get("pod", task.task_id)
    assert result.status is ResultStatus.SUCCEEDED
    assert stored_task.status is TaskStatus.SUCCEEDED
    assert repositories.results.list_for_task("pod", task.task_id) == [result]
    assert repositories.results.list_for_task("trading", task.task_id) == []
    events = repositories.events.list_for_task("pod", task.task_id)
    assert [(event.from_status, event.to_status) for event in events] == [
        (TaskStatus.CREATED, TaskStatus.QUEUED),
        (TaskStatus.QUEUED, TaskStatus.RUNNING),
        (TaskStatus.RUNNING, TaskStatus.SUCCEEDED),
    ]
    assert events[-1].details["result_id"] == result.result_id


def test_sqlite_result_survives_repository_restart(tmp_path):
    database_path = tmp_path / "kaios.db"
    repositories = SQLiteRepositories(database_path)
    repositories.workspaces.add(Workspace(workspace_id="pod", name="POD"))
    task = add_research_task(repositories, tmp_path)
    registry = AgentRegistry()
    registry.register(
        ProductIntelligenceAgent(
            extractor_factory=lambda **kwargs: StaticExtractor()
        )
    )
    result = AgentTaskService(registry, repositories).execute("pod", task.task_id)

    restarted = SQLiteRepositories(database_path)

    assert restarted.tasks.get("pod", task.task_id).status is TaskStatus.SUCCEEDED
    assert restarted.results.get("pod", result.result_id) == result
    assert len(restarted.events.list_for_task("pod", task.task_id)) == 3


@pytest.mark.parametrize("repository_kind", ["memory", "sqlite"])
def test_research_failure_persists_failed_result_and_lifecycle(
    repository_kind, tmp_path
):
    repositories, service = build_runtime(
        repository_kind,
        tmp_path,
        extractor_factory=lambda **kwargs: EmptyExtractor(),
    )
    task = add_research_task(repositories, tmp_path)

    result = service.execute("pod", task.task_id)

    assert result.status is ResultStatus.FAILED
    assert result.data["failed_stage"] == "evidence_collection"
    assert "no evidence was collected" in result.error
    assert repositories.tasks.get("pod", task.task_id).status is TaskStatus.FAILED
    assert repositories.results.list_for_task("pod", task.task_id) == [result]
    assert repositories.events.list_for_task("pod", task.task_id)[-1].to_status is (
        TaskStatus.FAILED
    )


@pytest.mark.parametrize("repository_kind", ["memory", "sqlite"])
def test_task_service_preserves_workspace_isolation(repository_kind, tmp_path):
    repositories, service = build_runtime(repository_kind, tmp_path)
    task = add_research_task(repositories, tmp_path)

    with pytest.raises(RecordNotFoundError, match="trading"):
        service.execute("trading", task.task_id)

    assert repositories.tasks.get("pod", task.task_id).status is TaskStatus.CREATED
    assert repositories.events.list_for_task("pod", task.task_id) == []


@pytest.mark.parametrize("repository_kind", ["memory", "sqlite"])
def test_unsupported_task_is_rejected_without_lifecycle_changes(
    repository_kind, tmp_path
):
    repositories, service = build_runtime(repository_kind, tmp_path)
    task = add_research_task(
        repositories, tmp_path, task_type="publish_listing"
    )

    with pytest.raises(UnsupportedTaskTypeError):
        service.execute("pod", task.task_id)

    assert repositories.tasks.get("pod", task.task_id).status is TaskStatus.CREATED
    assert repositories.events.list_for_task("pod", task.task_id) == []


@pytest.mark.parametrize("repository_kind", ["memory", "sqlite"])
def test_completion_failure_rolls_back_result_and_terminal_transition(
    repository_kind, tmp_path, monkeypatch
):
    repositories, service = build_runtime(repository_kind, tmp_path)
    task = add_research_task(repositories, tmp_path)
    original_add = repositories.events.add

    def fail_terminal_event(event):
        if event.to_status is TaskStatus.SUCCEEDED:
            raise RuntimeError("event persistence failed")
        return original_add(event)

    monkeypatch.setattr(repositories.events, "add", fail_terminal_event)

    with pytest.raises(RuntimeError, match="event persistence failed"):
        service.execute("pod", task.task_id)

    assert repositories.tasks.get("pod", task.task_id).status is TaskStatus.RUNNING
    assert repositories.results.list_for_task("pod", task.task_id) == []
    events = repositories.events.list_for_task("pod", task.task_id)
    assert [event.to_status for event in events] == [
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
    ]
