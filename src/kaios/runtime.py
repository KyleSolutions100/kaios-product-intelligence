"""Composition roots for KAIOS offline demo and live read-only research."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from kaios.agents.product_intelligence import ProductIntelligenceAgent
from kaios.agents.service import AgentTaskService
from kaios.agents.shells import build_initial_agent_registry
from kaios.approvals import ActionApprovalService, ProposalReview, SimulatedExecutionRecord
from kaios.core.contracts import (
    DEFAULT_WORKSPACE_ID,
    ActionProposal,
    AgentTask,
    ApprovalRequest,
    RiskClassification,
    TaskEvent,
    TaskStatus,
    Workspace,
    create_default_workspace,
)
from kaios.core.lifecycle import transition_task
from kaios.core.workspaces import WorkspaceBoundaryError
from kaios.model_providers import RulesModelProvider
from kaios.offline import OfflineDemoExtractor, write_offline_demo_reports
from kaios.orchestration import CEOOrchestrator, CEORequest, CEOResponse
from kaios.repositories import DEFAULT_DATABASE_PATH, RecordNotFoundError
from kaios.repositories.sqlite import SQLiteRepositories
from kaios.extractor import Extractor
from kaios.reporter import write_reports
from kaios.sources import validate_marketplace_configuration


DEMO_APPROVAL_TASK_TYPE = "demo_simulated_publish"
LIVE_EVIDENCE_SOURCE_TYPE = "LIVE"


@dataclass(frozen=True)
class DemoOutcome:
    response: CEOResponse
    proposal_review: ProposalReview | None = None
    resolved_approval: ApprovalRequest | None = None
    simulated_execution: SimulatedExecutionRecord | None = None


@dataclass
class KAIOSRuntime:
    repositories: SQLiteRepositories
    task_service: AgentTaskService
    orchestrator: CEOOrchestrator
    approvals: ActionApprovalService

    def ensure_default_workspace(self) -> Workspace:
        existing = self.repositories.workspaces.get(DEFAULT_WORKSPACE_ID)
        if existing is not None:
            return existing
        return self.repositories.workspaces.add(create_default_workspace())

    def create_workspace(
        self, workspace_id: str, name: str, description: str = ""
    ) -> Workspace:
        return self.repositories.workspaces.add(
            Workspace(
                workspace_id=workspace_id,
                name=name,
                description=description,
            )
        )

    def submit_product_research(
        self,
        *,
        workspace_id: str,
        seed: str,
        marketplace: str = "etsy",
        result_limit: int = 3,
        report_output_location: str | None = None,
    ) -> CEOResponse:
        return self.orchestrator.orchestrate(
            CEORequest(
                workspace_id=workspace_id,
                request_type="research_product_opportunities",
                seed=seed,
                marketplace=marketplace,
                result_limit=result_limit,
                report_output_location=report_output_location,
                model_provider="rules",
            )
        )

    def run_demo(
        self,
        *,
        seed: str,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        report_output_location: str | None = None,
        approval_choice: str = "pending",
    ) -> DemoOutcome:
        valid_approval_choices = {"none", "pending", "approve", "reject"}
        if approval_choice not in valid_approval_choices:
            raise ValueError(
                "approval_choice must be one of: none, pending, approve, reject"
            )
        if workspace_id == DEFAULT_WORKSPACE_ID:
            self.ensure_default_workspace()
        elif self.repositories.workspaces.get(workspace_id) is None:
            raise RecordNotFoundError(f"workspace not found: {workspace_id}")
        response = self.submit_product_research(
            workspace_id=workspace_id,
            seed=seed,
            marketplace="etsy",
            result_limit=3,
            report_output_location=report_output_location,
        )
        if approval_choice == "none":
            return DemoOutcome(response=response)

        review = self.create_demo_proposal(response)
        if review.approval is None:
            raise RuntimeError("demo risky action unexpectedly required no approval")
        if approval_choice == "pending":
            return DemoOutcome(response=response, proposal_review=review)
        if approval_choice == "reject":
            rejected = self.approvals.reject(
                workspace_id,
                review.approval.approval_id,
                actor_id="human_owner",
                reason="Human rejected the offline demo action",
            )
            return DemoOutcome(
                response=response,
                proposal_review=review,
                resolved_approval=rejected,
            )

        approved = self.approvals.approve(
            workspace_id,
            review.approval.approval_id,
            actor_id="human_owner",
            reason="Human approved simulation only",
        )
        execution = self.approvals.simulate_execution(
            review.proposal, approval_id=approved.approval_id
        )
        return DemoOutcome(
            response=response,
            proposal_review=review,
            resolved_approval=approved,
            simulated_execution=execution,
        )

    def create_demo_proposal(self, response: CEOResponse) -> ProposalReview:
        recommendation = response.recommendations[0] if response.recommendations else {}
        with self.repositories.transaction():
            task = self.repositories.tasks.add(
                AgentTask(
                    workspace_id=response.workspace_id,
                    task_type=DEMO_APPROVAL_TASK_TYPE,
                    assigned_agent="store_operations",
                    requested_by="human_owner",
                    parent_task_id=response.parent_task_id,
                    input_data={
                        "simulation_only": True,
                        "recommendation": recommendation,
                    },
                )
            )
            task = self._transition(task, TaskStatus.QUEUED, "demo action queued")
            task = self._transition(task, TaskStatus.RUNNING, "demo proposal prepared")
        proposal = ActionProposal.for_task(
            task,
            proposed_by_agent=task.assigned_agent,
            action_type="publish_listing",
            summary="Simulate publishing the recommended product listing",
            payload={
                "simulation_only": True,
                "marketplace": "etsy",
                "listing": {
                    "title": recommendation.get("title", "Demo product listing"),
                    "source_decision_id": response.decision_id,
                },
            },
            risk=RiskClassification.HIGH,
            is_public=True,
            is_reversible=False,
        )
        return self.approvals.create_proposal(proposal)

    def require_task(self, workspace_id: str, task_id: str) -> AgentTask:
        task = self.repositories.tasks.get(workspace_id, task_id)
        if task is not None:
            return task
        self._raise_missing_or_cross_workspace(
            workspace_id, task_id, self.repositories.tasks.get, "task"
        )

    def require_approval(
        self, workspace_id: str, approval_id: str
    ) -> ApprovalRequest:
        approval = self.repositories.approvals.get(workspace_id, approval_id)
        if approval is not None:
            return approval
        self._raise_missing_or_cross_workspace(
            workspace_id, approval_id, self.repositories.approvals.get, "approval"
        )

    def task_events(self, workspace_id: str, task_id: str) -> list[TaskEvent]:
        self.require_task(workspace_id, task_id)
        return self.repositories.events.list_for_task(workspace_id, task_id)

    def _raise_missing_or_cross_workspace(
        self, workspace_id: str, record_id: str, getter, label: str
    ) -> None:
        for workspace in self.repositories.workspaces.list():
            if workspace.workspace_id != workspace_id and getter(
                workspace.workspace_id, record_id
            ) is not None:
                raise WorkspaceBoundaryError(
                    f"{label} {record_id} belongs to workspace "
                    f"{workspace.workspace_id}, not {workspace_id}"
                )
        raise RecordNotFoundError(
            f"{label} not found in workspace {workspace_id}: {record_id}"
        )

    def _transition(
        self, task: AgentTask, status: TaskStatus, reason: str
    ) -> AgentTask:
        updated, event = transition_task(task, status, details={"reason": reason})
        stored = self.repositories.tasks.update(updated)
        self.repositories.events.add(event)
        return stored


def build_runtime(
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    *,
    default_report_output_location: str = "reports",
) -> KAIOSRuntime:
    repositories = SQLiteRepositories(database_path)
    product_agent = ProductIntelligenceAgent(
        provider=RulesModelProvider(),
        extractor_factory=OfflineDemoExtractor,
        report_writer=write_offline_demo_reports,
        default_report_output_location=default_report_output_location,
    )
    registry = build_initial_agent_registry(product_agent)
    task_service = AgentTaskService(registry, repositories)
    orchestrator = CEOOrchestrator(
        registry, repositories, task_service=task_service
    )
    approvals = ActionApprovalService(repositories)
    return KAIOSRuntime(
        repositories=repositories,
        task_service=task_service,
        orchestrator=orchestrator,
        approvals=approvals,
    )


def build_live_research_runtime(
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    *,
    marketplace: str = "etsy",
    default_report_output_location: str = "reports",
) -> KAIOSRuntime:
    """Build the official read-only research runtime after configuration checks."""

    validate_marketplace_configuration(marketplace)
    repositories = SQLiteRepositories(database_path)
    product_agent = ProductIntelligenceAgent(
        provider=RulesModelProvider(),
        extractor_factory=Extractor,
        report_writer=write_live_research_reports,
        default_report_output_location=default_report_output_location,
    )
    registry = build_initial_agent_registry(product_agent)
    task_service = AgentTaskService(registry, repositories)
    orchestrator = CEOOrchestrator(
        registry, repositories, task_service=task_service
    )
    approvals = ActionApprovalService(repositories)
    return KAIOSRuntime(
        repositories=repositories,
        task_service=task_service,
        orchestrator=orchestrator,
        approvals=approvals,
    )


def write_live_research_reports(seed, opportunities, config):
    """Label reports produced only after successful official API retrieval."""

    markdown_path, json_path = write_reports(seed, opportunities, config)
    disclaimer = (
        "> **Evidence mode: LIVE — official read-only marketplace API.** "
        "Popularity indicators are public signals, not verified sales.\n\n"
    )
    markdown_path.write_text(
        disclaimer + markdown_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    report_data = json.loads(json_path.read_text(encoding="utf-8"))
    report_data.update(
        {
            "evidence_mode": LIVE_EVIDENCE_SOURCE_TYPE,
            "disclaimer": (
                "Official read-only public marketplace evidence; popularity "
                "indicators are signals, not verified sales."
            ),
        }
    )
    json_path.write_text(
        json.dumps(report_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return markdown_path, json_path
