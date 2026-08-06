"""Deterministic CEO orchestration for workspace-scoped business requests."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol

from pydantic import ValidationError

from kaios.agents.product_intelligence import (
    PRODUCT_INTELLIGENCE_AGENT_ID,
    PRODUCT_RESEARCH_TASK_TYPE,
)
from kaios.agents.registry import AgentRegistry
from kaios.agents.service import AgentTaskService
from kaios.core.contracts import (
    AgentResult,
    AgentTask,
    DecisionRecord,
    ResultStatus,
    TaskStatus,
)
from kaios.core.lifecycle import transition_task
from kaios.repositories.interfaces import (
    DecisionRepository,
    EventRepository,
    RecordNotFoundError,
    ResultRepository,
    TaskRepository,
    WorkspaceRepository,
)

from .contracts import CEORequest, CEOResponse, CEOResponseStatus


CEO_ORCHESTRATOR_ID = "ceo_orchestrator"
CEO_PARENT_TASK_TYPE = "orchestrate_business_request"
RESEARCH_PRODUCT_OPPORTUNITIES = "research_product_opportunities"


class CEOOrchestrationError(RuntimeError):
    """Base error for rejected or invalid CEO requests."""


class CEORequestValidationError(CEOOrchestrationError):
    """Raised when a request does not satisfy the structured contract."""


class UnsupportedCEORequestError(CEOOrchestrationError):
    """Raised when deterministic routing has no matching specialist task."""


class AmbiguousCEORequestError(CEOOrchestrationError):
    """Raised when a request contains multiple or incomplete intents."""


class CEOApprovalForbiddenError(CEOOrchestrationError):
    """Raised whenever the CEO attempts to approve its own recommendation."""


class OrchestrationUnitOfWork(Protocol):
    workspaces: WorkspaceRepository
    tasks: TaskRepository
    results: ResultRepository
    decisions: DecisionRepository
    events: EventRepository

    def transaction(self) -> AbstractContextManager[Any]:
        """Return a rollback-capable repository transaction."""


class CEOOrchestrator:
    def __init__(
        self,
        registry: AgentRegistry,
        repositories: OrchestrationUnitOfWork,
        *,
        task_service: AgentTaskService | None = None,
    ) -> None:
        self._registry = registry
        self._repositories = repositories
        self._task_service = task_service or AgentTaskService(registry, repositories)

    @property
    def orchestrator_id(self) -> str:
        return CEO_ORCHESTRATOR_ID

    @property
    def supported_request_types(self) -> frozenset[str]:
        return frozenset({RESEARCH_PRODUCT_OPPORTUNITIES})

    @property
    def allowed_action_types(self) -> frozenset[str]:
        return frozenset()

    def orchestrate(self, request: CEORequest | dict[str, Any]) -> CEOResponse:
        structured_request = self._validate_request(request)
        seed = self._route_request(structured_request)
        if self._repositories.workspaces.get(structured_request.workspace_id) is None:
            raise RecordNotFoundError(
                f"workspace not found: {structured_request.workspace_id}"
            )

        parent, child = self._create_task_tree(structured_request, seed)
        try:
            specialist_result = self._task_service.execute(
                structured_request.workspace_id, child.task_id
            )
        except Exception as error:
            return self._finish_failure(
                structured_request,
                parent,
                child,
                specialist_result=None,
                error=f"{type(error).__name__}: {error}",
            )

        if specialist_result.status is ResultStatus.FAILED:
            return self._finish_failure(
                structured_request,
                parent,
                child,
                specialist_result=specialist_result,
                error=specialist_result.error or specialist_result.summary,
            )
        return self._finish_success(
            structured_request, parent, child, specialist_result
        )

    def approve_recommendation(
        self, *, workspace_id: str, decision_id: str
    ) -> None:
        """Reject self-approval; human approval execution is not implemented."""

        raise CEOApprovalForbiddenError(
            "the CEO orchestrator cannot approve its own recommendation; "
            "human approval is required"
        )

    def _validate_request(
        self, request: CEORequest | dict[str, Any]
    ) -> CEORequest:
        if isinstance(request, CEORequest):
            return request
        try:
            return CEORequest.model_validate(request)
        except ValidationError as error:
            raise CEORequestValidationError(
                f"invalid CEO request: {error.errors(include_url=False)}"
            ) from error

    def _route_request(self, request: CEORequest) -> str:
        normalized_type = " ".join(
            request.request_type.lower().replace("_", " ").replace("-", " ").split()
        )
        if any(separator in normalized_type for separator in (" and ", ",", "/", ";")):
            raise AmbiguousCEORequestError(
                "request contains multiple intents; submit one business request at a time"
            )
        if normalized_type != "research product opportunities":
            raise UnsupportedCEORequestError(
                f"unsupported CEO request type: {request.request_type}"
            )
        phrases = [value for value in (request.seed, request.research_objective) if value]
        if len(phrases) != 1:
            raise AmbiguousCEORequestError(
                "provide exactly one of seed or research_objective"
            )
        return phrases[0]

    def _create_task_tree(
        self, request: CEORequest, seed: str
    ) -> tuple[AgentTask, AgentTask]:
        parent_input = request.model_dump(mode="json")
        child_input: dict[str, Any] = {
            "seed": seed,
            "marketplace": request.marketplace,
            "result_limit": request.result_limit,
        }
        if request.report_output_location is not None:
            child_input["report_output_location"] = request.report_output_location
        if request.model_provider is not None:
            child_input["model_provider"] = request.model_provider

        with self._repositories.transaction():
            parent = self._repositories.tasks.add(
                AgentTask(
                    workspace_id=request.workspace_id,
                    task_type=CEO_PARENT_TASK_TYPE,
                    assigned_agent=self.orchestrator_id,
                    requested_by="human_owner",
                    input_data=parent_input,
                )
            )
            parent = self._transition(
                parent, TaskStatus.QUEUED, reason="human request accepted"
            )
            parent = self._transition(
                parent, TaskStatus.RUNNING, reason="CEO orchestration started"
            )
            child = self._repositories.tasks.add(
                AgentTask(
                    workspace_id=request.workspace_id,
                    task_type=PRODUCT_RESEARCH_TASK_TYPE,
                    assigned_agent=PRODUCT_INTELLIGENCE_AGENT_ID,
                    requested_by=self.orchestrator_id,
                    parent_task_id=parent.task_id,
                    input_data=child_input,
                )
            )
        return parent, child

    def _finish_success(
        self,
        request: CEORequest,
        parent: AgentTask,
        child: AgentTask,
        specialist_result: AgentResult,
    ) -> CEOResponse:
        summary = (
            "CEO review completed: "
            f"{len(specialist_result.recommendations)} recommendations prepared "
            "for human review"
        )
        with self._repositories.transaction():
            current_parent = self._require_running_parent(parent)
            ceo_result = self._repositories.results.add(
                AgentResult.for_task(
                    current_parent,
                    agent_id=self.orchestrator_id,
                    status=ResultStatus.SUCCEEDED,
                    summary=summary,
                    data={
                        "child_task_id": child.task_id,
                        "specialist_result_id": specialist_result.result_id,
                        "specialist_summary": specialist_result.summary,
                        "metrics": specialist_result.data.get("metrics", {}),
                        "reports": specialist_result.data.get("reports", {}),
                        "requires_human_approval": True,
                    },
                    evidence=specialist_result.evidence,
                    recommendations=specialist_result.recommendations,
                    proposed_actions=[],
                )
            )
            decision = self._repositories.decisions.add(
                DecisionRecord.for_task(
                    current_parent,
                    decision_type="product_opportunity_recommendation",
                    summary=summary,
                    rationale=(
                        "Deterministic routing collected Product Intelligence evidence; "
                        "the recommendation requires human review and executes no action."
                    ),
                    data={
                        "ceo_result_id": ceo_result.result_id,
                        "child_task_id": child.task_id,
                        "specialist_result_id": specialist_result.result_id,
                        "recommendations": specialist_result.recommendations,
                        "evidence": specialist_result.evidence,
                        "metrics": specialist_result.data.get("metrics", {}),
                        "reports": specialist_result.data.get("reports", {}),
                        "requires_human_approval": True,
                    },
                )
            )
            self._transition(
                current_parent,
                TaskStatus.SUCCEEDED,
                reason="specialist recommendation recorded",
                details={
                    "ceo_result_id": ceo_result.result_id,
                    "child_task_id": child.task_id,
                    "specialist_result_id": specialist_result.result_id,
                    "decision_id": decision.decision_id,
                },
            )

        return CEOResponse(
            workspace_id=request.workspace_id,
            request_type=RESEARCH_PRODUCT_OPPORTUNITIES,
            status=CEOResponseStatus.SUCCEEDED,
            parent_task_id=parent.task_id,
            child_task_id=child.task_id,
            specialist_result_id=specialist_result.result_id,
            ceo_result_id=ceo_result.result_id,
            decision_id=decision.decision_id,
            summary=summary,
            evidence=specialist_result.evidence,
            recommendations=specialist_result.recommendations,
            requires_human_approval=True,
        )

    def _finish_failure(
        self,
        request: CEORequest,
        parent: AgentTask,
        child: AgentTask,
        *,
        specialist_result: AgentResult | None,
        error: str,
    ) -> CEOResponse:
        summary = "CEO orchestration failed because Product Intelligence failed"
        with self._repositories.transaction():
            current_parent = self._require_running_parent(parent)
            ceo_result = self._repositories.results.add(
                AgentResult.for_task(
                    current_parent,
                    agent_id=self.orchestrator_id,
                    status=ResultStatus.FAILED,
                    summary=summary,
                    error=error,
                    data={
                        "child_task_id": child.task_id,
                        "specialist_result_id": (
                            specialist_result.result_id if specialist_result else None
                        ),
                        "specialist_error": error,
                        "requires_human_approval": False,
                    },
                    evidence=specialist_result.evidence if specialist_result else [],
                    recommendations=[],
                    proposed_actions=[],
                )
            )
            self._transition(
                current_parent,
                TaskStatus.FAILED,
                reason="specialist execution failed",
                details={
                    "ceo_result_id": ceo_result.result_id,
                    "child_task_id": child.task_id,
                    "specialist_result_id": (
                        specialist_result.result_id if specialist_result else None
                    ),
                    "error": error,
                },
            )

        return CEOResponse(
            workspace_id=request.workspace_id,
            request_type=RESEARCH_PRODUCT_OPPORTUNITIES,
            status=CEOResponseStatus.FAILED,
            parent_task_id=parent.task_id,
            child_task_id=child.task_id,
            specialist_result_id=(
                specialist_result.result_id if specialist_result else None
            ),
            ceo_result_id=ceo_result.result_id,
            decision_id=None,
            summary=summary,
            evidence=specialist_result.evidence if specialist_result else [],
            recommendations=[],
            error=error,
            requires_human_approval=False,
        )

    def _require_running_parent(self, parent: AgentTask) -> AgentTask:
        current = self._repositories.tasks.get(parent.workspace_id, parent.task_id)
        if current is None:
            raise RecordNotFoundError(f"parent task not found: {parent.task_id}")
        if current.status is not TaskStatus.RUNNING:
            raise CEOOrchestrationError(
                f"parent task must be running, not {current.status.value}"
            )
        return current

    def _transition(
        self,
        task: AgentTask,
        target_status: TaskStatus,
        *,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> AgentTask:
        event_details = {"reason": reason, **(details or {})}
        updated_task, event = transition_task(
            task, target_status, details=event_details
        )
        stored_task = self._repositories.tasks.update(updated_task)
        self._repositories.events.add(event)
        return stored_task
