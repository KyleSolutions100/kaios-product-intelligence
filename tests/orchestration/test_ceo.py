from pathlib import Path

import pytest

from kaios.agents import ProductIntelligenceAgent, build_initial_agent_registry
from kaios.core.contracts import ResultStatus, TaskStatus, Workspace
from kaios.orchestration import (
    AmbiguousCEORequestError,
    CEOApprovalForbiddenError,
    CEOOrchestrator,
    CEOResponseStatus,
    UnsupportedCEORequestError,
)
from kaios.repositories.interfaces import RecordNotFoundError
from kaios.repositories.memory import InMemoryRepositories
from kaios.repositories.sqlite import SQLiteRepositories


EVIDENCE = [
    {
        "title": "Popular Eco Invitation",
        "url": "https://example.invalid/listing",
        "content": "Popular recycled invitation with many reviews " * 5,
        "source": "Mock marketplace",
        "source_url": "https://example.invalid",
        "source_type": "mock",
        "price_range": "£12-£24",
        "competitor_count_estimate": "24 listings",
    }
]


class StaticExtractor:
    def __init__(self, evidence):
        self._evidence = evidence

    def gather(self, seed):
        return self._evidence


def build_runtime(repository_kind, tmp_path, *, evidence=EVIDENCE):
    if repository_kind == "memory":
        repositories = InMemoryRepositories()
    else:
        repositories = SQLiteRepositories(tmp_path / "kaios.db")
    repositories.workspaces.add(Workspace(workspace_id="pod", name="POD"))
    repositories.workspaces.add(
        Workspace(workspace_id="trading", name="Trading")
    )
    product_agent = ProductIntelligenceAgent(
        extractor_factory=lambda **kwargs: StaticExtractor(evidence)
    )
    registry = build_initial_agent_registry(product_agent)
    return repositories, CEOOrchestrator(registry, repositories)


def valid_request(tmp_path):
    return {
        "workspace_id": "pod",
        "request_type": "Research product opportunities",
        "seed": "eco wedding invitation",
        "marketplace": "etsy",
        "result_limit": 5,
        "report_output_location": str(tmp_path / "reports"),
        "model_provider": "rules",
    }


@pytest.mark.parametrize("repository_kind", ["memory", "sqlite"])
def test_valid_request_routes_and_persists_complete_task_tree(
    repository_kind, tmp_path, monkeypatch
):
    def fail_if_litellm_loads():
        raise AssertionError("CEO offline flow must not load LiteLLM")

    monkeypatch.delenv("KAIOS_LLM_API_KEY", raising=False)
    monkeypatch.setattr(
        "kaios.model_providers.litellm._load_litellm_completion",
        fail_if_litellm_loads,
    )
    repositories, orchestrator = build_runtime(repository_kind, tmp_path)

    response = orchestrator.orchestrate(valid_request(tmp_path))

    assert response.status is CEOResponseStatus.SUCCEEDED
    assert response.workspace_id == "pod"
    assert response.requires_human_approval is True
    assert response.decision_id is not None
    assert len(response.recommendations) == 1
    assert len(response.evidence) == 1
    parent = repositories.tasks.get("pod", response.parent_task_id)
    child = repositories.tasks.get("pod", response.child_task_id)
    assert parent.status is TaskStatus.SUCCEEDED
    assert parent.assigned_agent == "ceo_orchestrator"
    assert child.status is TaskStatus.SUCCEEDED
    assert child.assigned_agent == "product_intelligence"
    assert child.parent_task_id == parent.task_id
    assert child.workspace_id == parent.workspace_id == "pod"
    assert child.input_data["seed"] == "eco wedding invitation"
    assert child.input_data["model_provider"] == "rules"

    parent_events = repositories.events.list_for_task("pod", parent.task_id)
    child_events = repositories.events.list_for_task("pod", child.task_id)
    assert [(event.from_status, event.to_status) for event in parent_events] == [
        (TaskStatus.CREATED, TaskStatus.QUEUED),
        (TaskStatus.QUEUED, TaskStatus.RUNNING),
        (TaskStatus.RUNNING, TaskStatus.SUCCEEDED),
    ]
    assert [(event.from_status, event.to_status) for event in child_events] == [
        (TaskStatus.CREATED, TaskStatus.QUEUED),
        (TaskStatus.QUEUED, TaskStatus.RUNNING),
        (TaskStatus.RUNNING, TaskStatus.SUCCEEDED),
    ]

    specialist_results = repositories.results.list_for_task("pod", child.task_id)
    ceo_results = repositories.results.list_for_task("pod", parent.task_id)
    assert [result.result_id for result in specialist_results] == [
        response.specialist_result_id
    ]
    assert [result.result_id for result in ceo_results] == [response.ceo_result_id]
    assert all(result.proposed_actions == [] for result in specialist_results)
    assert all(result.proposed_actions == [] for result in ceo_results)
    assert Path(specialist_results[0].data["reports"]["markdown"]).exists()
    assert Path(specialist_results[0].data["reports"]["json"]).exists()

    decisions = repositories.decisions.list("pod")
    assert [decision.decision_id for decision in decisions] == [response.decision_id]
    assert decisions[0].related_task_id == parent.task_id
    assert decisions[0].decision_type == "product_opportunity_recommendation"
    assert decisions[0].data["specialist_result_id"] == response.specialist_result_id
    assert decisions[0].data["recommendations"] == response.recommendations
    assert decisions[0].data["evidence"] == response.evidence
    assert decisions[0].data["requires_human_approval"] is True
    assert repositories.approvals.list("pod") == []
    assert orchestrator.allowed_action_types == frozenset()


@pytest.mark.parametrize("repository_kind", ["memory", "sqlite"])
def test_specialist_failure_fails_parent_without_success_decision(
    repository_kind, tmp_path
):
    repositories, orchestrator = build_runtime(
        repository_kind, tmp_path, evidence=[]
    )

    response = orchestrator.orchestrate(valid_request(tmp_path))

    assert response.status is CEOResponseStatus.FAILED
    assert response.decision_id is None
    assert response.requires_human_approval is False
    assert "no evidence was collected" in response.error
    parent = repositories.tasks.get("pod", response.parent_task_id)
    child = repositories.tasks.get("pod", response.child_task_id)
    assert parent.status is TaskStatus.FAILED
    assert child.status is TaskStatus.FAILED
    child_result = repositories.results.get("pod", response.specialist_result_id)
    ceo_result = repositories.results.get("pod", response.ceo_result_id)
    assert child_result.status is ResultStatus.FAILED
    assert ceo_result.status is ResultStatus.FAILED
    assert child_result.error in ceo_result.error
    assert repositories.decisions.list("pod") == []
    assert repositories.approvals.list("pod") == []
    assert repositories.events.list_for_task("pod", parent.task_id)[-1].to_status is (
        TaskStatus.FAILED
    )
    assert repositories.events.list_for_task("pod", child.task_id)[-1].to_status is (
        TaskStatus.FAILED
    )


@pytest.mark.parametrize("repository_kind", ["memory", "sqlite"])
def test_workspace_isolation_is_enforced_before_tasks_are_created(
    repository_kind, tmp_path
):
    repositories, orchestrator = build_runtime(repository_kind, tmp_path)
    request = valid_request(tmp_path)
    request["workspace_id"] = "missing"

    with pytest.raises(RecordNotFoundError, match="missing"):
        orchestrator.orchestrate(request)

    assert repositories.tasks.list("pod") == []
    assert repositories.tasks.list("trading") == []
    assert repositories.decisions.list("pod") == []
    assert repositories.decisions.list("trading") == []


@pytest.mark.parametrize("repository_kind", ["memory", "sqlite"])
def test_unsupported_and_ambiguous_requests_create_no_audit_records(
    repository_kind, tmp_path
):
    repositories, orchestrator = build_runtime(repository_kind, tmp_path)
    unsupported = valid_request(tmp_path)
    unsupported["request_type"] = "Launch advertising campaign"

    with pytest.raises(UnsupportedCEORequestError, match="unsupported"):
        orchestrator.orchestrate(unsupported)

    multiple_intents = valid_request(tmp_path)
    multiple_intents["request_type"] = "Research product opportunities and publish"
    with pytest.raises(AmbiguousCEORequestError, match="multiple intents"):
        orchestrator.orchestrate(multiple_intents)

    conflicting_objectives = valid_request(tmp_path)
    conflicting_objectives["research_objective"] = "another objective"
    with pytest.raises(AmbiguousCEORequestError, match="exactly one"):
        orchestrator.orchestrate(conflicting_objectives)

    missing_objective = valid_request(tmp_path)
    missing_objective["seed"] = None
    with pytest.raises(AmbiguousCEORequestError, match="exactly one"):
        orchestrator.orchestrate(missing_objective)

    assert repositories.tasks.list("pod") == []
    assert repositories.results.list_for_task("pod", "missing") == []
    assert repositories.decisions.list("pod") == []


def test_ceo_explicitly_rejects_self_approval(tmp_path):
    repositories, orchestrator = build_runtime("memory", tmp_path)
    response = orchestrator.orchestrate(valid_request(tmp_path))

    with pytest.raises(CEOApprovalForbiddenError, match="human approval"):
        orchestrator.approve_recommendation(
            workspace_id="pod", decision_id=response.decision_id
        )

    assert repositories.approvals.list("pod") == []
    assert repositories.decisions.get("pod", response.decision_id) is not None


def test_research_objective_is_deterministically_mapped_to_child_seed(tmp_path):
    repositories, orchestrator = build_runtime("memory", tmp_path)
    request = valid_request(tmp_path)
    request["seed"] = None
    request["research_objective"] = "find low-competition recycled invitations"

    response = orchestrator.orchestrate(request)

    child = repositories.tasks.get("pod", response.child_task_id)
    assert response.status is CEOResponseStatus.SUCCEEDED
    assert child.input_data["seed"] == (
        "find low-competition recycled invitations"
    )


def test_sqlite_orchestration_survives_repository_restart(tmp_path):
    database_path = tmp_path / "kaios.db"
    repositories = SQLiteRepositories(database_path)
    repositories.workspaces.add(Workspace(workspace_id="pod", name="POD"))
    product_agent = ProductIntelligenceAgent(
        extractor_factory=lambda **kwargs: StaticExtractor(EVIDENCE)
    )
    orchestrator = CEOOrchestrator(
        build_initial_agent_registry(product_agent), repositories
    )

    response = orchestrator.orchestrate(valid_request(tmp_path))
    restarted = SQLiteRepositories(database_path)

    assert restarted.tasks.get("pod", response.parent_task_id).status is (
        TaskStatus.SUCCEEDED
    )
    assert restarted.tasks.get("pod", response.child_task_id).parent_task_id == (
        response.parent_task_id
    )
    assert restarted.results.get("pod", response.specialist_result_id) is not None
    assert restarted.results.get("pod", response.ceo_result_id) is not None
    assert restarted.decisions.get("pod", response.decision_id) is not None
