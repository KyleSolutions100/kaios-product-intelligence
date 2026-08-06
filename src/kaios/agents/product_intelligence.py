"""Product Intelligence agent wrapping the existing research workflow."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from kaios.analyzer import synthesize
from kaios.core.contracts import (
    AgentResult,
    AgentTask,
    ResultStatus,
    TaskStatus,
)
from kaios.extractor import Extractor
from kaios.model_providers import ModelProvider, RulesModelProvider
from kaios.models import ModelProviderName, Opportunity, ReportConfig
from kaios.reporter import write_reports

from .base import BaseAgent


PRODUCT_INTELLIGENCE_AGENT_ID = "product_intelligence"
PRODUCT_RESEARCH_TASK_TYPE = "product_research"


class AgentTaskValidationError(ValueError):
    """Raised when a task cannot legally be handled by this agent."""


class ResearchFailure(RuntimeError):
    """Raised for a failed research stage, including missing evidence."""


class EvidenceExtractor(Protocol):
    def gather(self, seed: str) -> list[dict[str, Any]]:
        """Collect structured evidence for a search seed."""


ExtractorFactory = Callable[..., EvidenceExtractor]
AnalyzerFunction = Callable[..., list[Opportunity]]
ReportWriter = Callable[
    [str, list[Opportunity], ReportConfig], tuple[Path, Path]
]


class ProductResearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    seed: str = Field(min_length=1)
    marketplace: str = Field(default="etsy", min_length=1)
    result_limit: int = Field(default=12, ge=1, le=100)
    report_output_location: str | None = Field(default=None, min_length=1)
    model_provider: ModelProviderName | None = None


class ProductIntelligenceAgent(BaseAgent):
    def __init__(
        self,
        *,
        provider: ModelProvider | None = None,
        allowed_provider_overrides: Mapping[str, ModelProvider] | None = None,
        extractor_factory: ExtractorFactory = Extractor,
        analyzer: AnalyzerFunction = synthesize,
        report_writer: ReportWriter = write_reports,
        default_report_output_location: str = "reports",
    ) -> None:
        self._provider = provider or RulesModelProvider()
        self._allowed_provider_overrides = dict(allowed_provider_overrides or {})
        self._extractor_factory = extractor_factory
        self._analyzer = analyzer
        self._report_writer = report_writer
        self._default_report_output_location = default_report_output_location

    @property
    def agent_id(self) -> str:
        return PRODUCT_INTELLIGENCE_AGENT_ID

    @property
    def supported_task_types(self) -> frozenset[str]:
        return frozenset({PRODUCT_RESEARCH_TASK_TYPE})

    @property
    def allowed_action_types(self) -> frozenset[str]:
        return frozenset()

    def handle(self, task: AgentTask) -> AgentResult:
        self._validate_task(task)
        try:
            research_input = ProductResearchInput.model_validate(task.input_data)
            provider = self._select_provider(research_input.model_provider)
        except (ValidationError, ValueError) as error:
            return self._failed_result(task, "input_validation", error)

        evidence: list[dict[str, Any]] = []
        try:
            extractor = self._extractor_factory(
                marketplace=research_input.marketplace,
                limit=research_input.result_limit,
            )
            evidence = extractor.gather(research_input.seed)
            if not evidence:
                raise ResearchFailure("no evidence was collected")
        except Exception as error:
            return self._failed_result(task, "evidence_collection", error)

        try:
            opportunities = self._analyzer(
                evidence,
                research_input.seed,
                provider=provider,
            )
        except Exception as error:
            return self._failed_result(
                task, "analysis", error, evidence=evidence, provider=provider
            )

        output_location = (
            research_input.report_output_location
            or self._default_report_output_location
        )
        report_config = ReportConfig(
            marketplace=research_input.marketplace,
            seed=research_input.seed,
            search_limit=research_input.result_limit,
            output_dir=output_location,
        )
        try:
            markdown_path, json_path = self._report_writer(
                research_input.seed, opportunities, report_config
            )
        except Exception as error:
            return self._failed_result(
                task,
                "report_generation",
                error,
                evidence=evidence,
                provider=provider,
            )

        recommendation_data = [
            {
                "title": opportunity.title,
                "recommended": opportunity.recommended,
                "confidence": opportunity.confidence,
                "why_recommended": opportunity.why_recommended,
            }
            for opportunity in opportunities
        ]
        recommended_count = sum(
            opportunity.recommended for opportunity in opportunities
        )
        opportunity_count = len(opportunities)
        summary = (
            f"Product research completed with {opportunity_count} opportunities"
            if opportunity_count
            else "Product research completed with zero genuine opportunities"
        )
        return AgentResult.for_task(
            task,
            agent_id=self.agent_id,
            status=ResultStatus.SUCCEEDED,
            summary=summary,
            data={
                "task_input": research_input.model_dump(mode="json"),
                "provider": provider.provider_id,
                "metrics": {
                    "evidence_count": len(evidence),
                    "opportunity_count": opportunity_count,
                    "recommended_count": recommended_count,
                },
                "reports": {
                    "markdown": str(markdown_path),
                    "json": str(json_path),
                },
                "opportunities": [
                    opportunity.model_dump(mode="json")
                    for opportunity in opportunities
                ],
                "zero_opportunities": opportunity_count == 0,
            },
            evidence=_evidence_references(evidence),
            recommendations=recommendation_data,
            proposed_actions=[],
        )

    def _validate_task(self, task: AgentTask) -> None:
        if task.assigned_agent != self.agent_id:
            raise AgentTaskValidationError(
                f"task is assigned to {task.assigned_agent}, not {self.agent_id}"
            )
        if task.task_type not in self.supported_task_types:
            raise AgentTaskValidationError(
                f"unsupported task type for {self.agent_id}: {task.task_type}"
            )
        if task.status is not TaskStatus.RUNNING:
            raise AgentTaskValidationError(
                f"product research task must be running, not {task.status.value}"
            )

    def _select_provider(self, requested: ModelProviderName | None) -> ModelProvider:
        if requested is None or requested == self._provider.provider_id:
            return self._provider
        provider = self._allowed_provider_overrides.get(requested)
        if provider is None:
            raise ValueError(f"model provider override is not permitted: {requested}")
        return provider

    def _failed_result(
        self,
        task: AgentTask,
        stage: str,
        error: Exception,
        *,
        evidence: list[dict[str, Any]] | None = None,
        provider: ModelProvider | None = None,
    ) -> AgentResult:
        error_text = f"{type(error).__name__}: {error}"
        return AgentResult.for_task(
            task,
            agent_id=self.agent_id,
            status=ResultStatus.FAILED,
            summary=f"Product research failed during {stage}",
            error=error_text,
            data={
                "failed_stage": stage,
                "provider": provider.provider_id if provider else None,
                "metrics": {
                    "evidence_count": len(evidence or []),
                    "opportunity_count": 0,
                    "recommended_count": 0,
                },
            },
            evidence=_evidence_references(evidence or []),
            recommendations=[],
            proposed_actions=[],
        )


def _evidence_references(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reference_fields = ("title", "url", "source", "source_url", "source_type")
    return [
        {field: item[field] for field in reference_fields if item.get(field)}
        for item in evidence
        if isinstance(item, dict)
    ]
