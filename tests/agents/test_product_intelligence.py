from pathlib import Path

import pytest

from kaios.agents import (
    PRODUCT_INTELLIGENCE_AGENT_ID,
    PRODUCT_RESEARCH_TASK_TYPE,
    AgentTaskValidationError,
    ProductIntelligenceAgent,
)
from kaios.core.contracts import AgentTask, ResultStatus, TaskStatus
from kaios.model_providers import FakeModelProvider


EVIDENCE = [
    {
        "title": "Eco Wedding Invitation",
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


class FailingExtractor:
    def gather(self, seed):
        raise RuntimeError("source unavailable")


def extractor_factory(evidence):
    def factory(*, marketplace, limit):
        return StaticExtractor(evidence)

    return factory


def running_task(tmp_path: Path, **input_updates) -> AgentTask:
    input_data = {
        "seed": "eco wedding invitation",
        "marketplace": "etsy",
        "result_limit": 5,
        "report_output_location": str(tmp_path / "reports"),
    }
    input_data.update(input_updates)
    return AgentTask(
        workspace_id="pod",
        task_type=PRODUCT_RESEARCH_TASK_TYPE,
        assigned_agent=PRODUCT_INTELLIGENCE_AGENT_ID,
        status=TaskStatus.RUNNING,
        input_data=input_data,
    )


def test_supported_research_task_runs_offline_and_generates_structured_reports(
    tmp_path, monkeypatch
):
    def fail_if_litellm_loads():
        raise AssertionError("offline Product Intelligence must not load LiteLLM")

    monkeypatch.delenv("KAIOS_LLM_API_KEY", raising=False)
    monkeypatch.setattr(
        "kaios.model_providers.litellm._load_litellm_completion",
        fail_if_litellm_loads,
    )
    agent = ProductIntelligenceAgent(
        extractor_factory=extractor_factory(EVIDENCE)
    )
    task = running_task(tmp_path)

    result = agent.handle(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.workspace_id == task.workspace_id
    assert result.task_id == task.task_id
    assert result.agent_id == PRODUCT_INTELLIGENCE_AGENT_ID
    assert result.data["provider"] == "rules"
    assert result.data["metrics"] == {
        "evidence_count": 1,
        "opportunity_count": 1,
        "recommended_count": 1,
    }
    assert result.evidence == [
        {
            "title": "Eco Wedding Invitation",
            "url": "https://example.invalid/listing",
            "source": "Mock marketplace",
            "source_url": "https://example.invalid",
            "source_type": "mock",
        }
    ]
    assert len(result.recommendations) == 1
    assert result.proposed_actions == []
    assert agent.allowed_action_types == frozenset()
    assert Path(result.data["reports"]["markdown"]).exists()
    assert Path(result.data["reports"]["json"]).exists()


@pytest.mark.parametrize(
    ("factory", "error_text"),
    [
        (lambda **kwargs: FailingExtractor(), "source unavailable"),
        (extractor_factory([]), "no evidence was collected"),
    ],
)
def test_research_collection_failures_return_failed_results(
    tmp_path, factory, error_text
):
    agent = ProductIntelligenceAgent(extractor_factory=factory)

    result = agent.handle(running_task(tmp_path))

    assert result.status is ResultStatus.FAILED
    assert result.data["failed_stage"] == "evidence_collection"
    assert error_text in result.error
    assert result.proposed_actions == []
    assert not (tmp_path / "reports").exists()


def test_zero_genuine_opportunities_is_success_and_still_generates_reports(tmp_path):
    agent = ProductIntelligenceAgent(
        provider=FakeModelProvider(output=[]),
        extractor_factory=extractor_factory(EVIDENCE),
    )

    result = agent.handle(running_task(tmp_path))

    assert result.status is ResultStatus.SUCCEEDED
    assert result.error is None
    assert result.data["zero_opportunities"] is True
    assert result.data["metrics"]["evidence_count"] == 1
    assert result.data["metrics"]["opportunity_count"] == 0
    assert result.recommendations == []
    assert Path(result.data["reports"]["markdown"]).exists()
    assert Path(result.data["reports"]["json"]).exists()


def test_provider_override_requires_explicit_agent_permission(tmp_path):
    agent = ProductIntelligenceAgent(
        extractor_factory=extractor_factory(EVIDENCE)
    )

    result = agent.handle(running_task(tmp_path, model_provider="fake"))

    assert result.status is ResultStatus.FAILED
    assert result.data["failed_stage"] == "input_validation"
    assert "not permitted" in result.error


def test_agent_validates_assignment_task_type_and_running_state(tmp_path):
    agent = ProductIntelligenceAgent(
        extractor_factory=extractor_factory(EVIDENCE)
    )
    task = running_task(tmp_path)

    with pytest.raises(AgentTaskValidationError, match="assigned"):
        agent.handle(task.model_copy(update={"assigned_agent": "other"}))
    with pytest.raises(AgentTaskValidationError, match="unsupported"):
        agent.handle(task.model_copy(update={"task_type": "publish_listing"}))
    with pytest.raises(AgentTaskValidationError, match="must be running"):
        agent.handle(task.model_copy(update={"status": TaskStatus.CREATED}))
