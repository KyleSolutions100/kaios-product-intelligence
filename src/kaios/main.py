"""Command-line interface for the offline-first Phase 1 KAIOS workflow."""

from __future__ import annotations

import json
import sqlite3
from functools import wraps
from pathlib import Path
from typing import Any, Callable, TypeVar

import typer

from kaios.approvals import ApprovalWorkflowError
from kaios.config import load_config
from kaios.core.contracts import DEFAULT_WORKSPACE_ID, ApprovalStatus
from kaios.core.workspaces import WorkspaceBoundaryError
from kaios.offline import OFFLINE_DEMO_SOURCE_TYPE
from kaios.orchestration import CEOOrchestrationError, CEOResponse
from kaios.repositories import (
    DEFAULT_DATABASE_PATH,
    DuplicateRecordError,
    RecordNotFoundError,
    RepositoryError,
)
from kaios.runtime import DemoOutcome, KAIOSRuntime, build_runtime


app = typer.Typer(
    help="KAIOS Phase 1 — workspace-safe, offline AI business operations.",
    no_args_is_help=True,
)
workspace_app = typer.Typer(help="Create and inspect business workspaces.")
task_app = typer.Typer(help="Inspect persisted tasks and audit events.")
approval_app = typer.Typer(help="Review and resolve exact action approvals.")
decision_app = typer.Typer(help="Inspect recorded CEO decisions.")
app.add_typer(workspace_app, name="workspace")
app.add_typer(task_app, name="task")
app.add_typer(approval_app, name="approval")
app.add_typer(decision_app, name="decision")


CommandFunction = TypeVar("CommandFunction", bound=Callable[..., Any])
CLI_ERRORS = (
    ApprovalWorkflowError,
    CEOOrchestrationError,
    DuplicateRecordError,
    RecordNotFoundError,
    RepositoryError,
    WorkspaceBoundaryError,
    sqlite3.Error,
    OSError,
    ValueError,
)


def guarded(command: CommandFunction) -> CommandFunction:
    """Render domain and storage failures as clear one-line CLI errors."""

    @wraps(command)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return command(*args, **kwargs)
        except CLI_ERRORS as error:
            typer.echo(f"Error: {error}", err=True)
            raise typer.Exit(code=1) from error

    return wrapper  # type: ignore[return-value]


def _runtime(database: Path) -> KAIOSRuntime:
    runtime = build_runtime(database)
    runtime.ensure_default_workspace()
    return runtime


@workspace_app.command("create")
@guarded
def workspace_create(
    workspace_id: str = typer.Argument(..., help="Stable workspace identifier."),
    name: str = typer.Option(..., "--name", help="Human-readable business name."),
    description: str = typer.Option("", help="Short business description."),
    database: Path = typer.Option(DEFAULT_DATABASE_PATH, "--database", "-d"),
) -> None:
    workspace = _runtime(database).create_workspace(workspace_id, name, description)
    typer.echo(f"Created workspace: {workspace.workspace_id} — {workspace.name}")


@workspace_app.command("list")
@guarded
def workspace_list(
    database: Path = typer.Option(DEFAULT_DATABASE_PATH, "--database", "-d"),
) -> None:
    workspaces = _runtime(database).repositories.workspaces.list()
    typer.echo("Workspaces")
    for workspace in workspaces:
        typer.echo(
            f"- {workspace.workspace_id} | {workspace.status.value} | {workspace.name}"
        )


@app.command("research")
@guarded
def research(
    seed: str = typer.Argument(..., help="Product seed or research objective."),
    workspace: str = typer.Option(DEFAULT_WORKSPACE_ID, "--workspace", "-w"),
    config: Path | None = typer.Option(
        None, "--config", help="Compatible Phase 1 YAML configuration file."
    ),
    marketplace: str | None = typer.Option(None, help="Marketplace override."),
    limit: int | None = typer.Option(None, min=1, max=100),
    output: Path | None = typer.Option(None, help="Report output directory."),
    database: Path = typer.Option(DEFAULT_DATABASE_PATH, "--database", "-d"),
) -> None:
    configured_marketplace = "etsy"
    configured_limit = 3
    configured_output: str | None = None
    if config is not None:
        if not config.is_file():
            raise ValueError(f"configuration file not found: {config}")
        loaded = load_config(str(config))
        provider = loaded.provider_for_agent("product_intelligence")
        if provider != "rules":
            raise ValueError(
                "the offline CEO research command supports only the rules model "
                f"provider, not {provider}"
            )
        configured_marketplace = loaded.marketplace
        configured_limit = loaded.search_limit
        configured_output = loaded.output_dir
    runtime = _runtime(database)
    response = runtime.submit_product_research(
        workspace_id=workspace,
        seed=seed,
        marketplace=marketplace or configured_marketplace,
        result_limit=limit if limit is not None else configured_limit,
        report_output_location=(
            str(output) if output is not None else configured_output
        ),
    )
    _display_briefing(runtime, response)


@task_app.command("list")
@guarded
def task_list(
    workspace: str = typer.Option(DEFAULT_WORKSPACE_ID, "--workspace", "-w"),
    database: Path = typer.Option(DEFAULT_DATABASE_PATH, "--database", "-d"),
) -> None:
    runtime = _runtime(database)
    _require_workspace(runtime, workspace)
    typer.echo(f"Tasks — workspace {workspace}")
    for task in runtime.repositories.tasks.list(workspace):
        parent = task.parent_task_id or "none"
        typer.echo(
            f"- {task.task_id} | {task.status.value} | {task.assigned_agent} | "
            f"{task.task_type} | parent={parent}"
        )


@task_app.command("show")
@guarded
def task_show(
    task_id: str = typer.Argument(...),
    workspace: str = typer.Option(DEFAULT_WORKSPACE_ID, "--workspace", "-w"),
    database: Path = typer.Option(DEFAULT_DATABASE_PATH, "--database", "-d"),
) -> None:
    runtime = _runtime(database)
    task = runtime.require_task(workspace, task_id)
    typer.echo(f"Task: {task.task_id}")
    typer.echo(f"Workspace: {task.workspace_id}")
    typer.echo(f"Agent: {task.assigned_agent}")
    typer.echo(f"Type: {task.task_type}")
    typer.echo(f"Status: {task.status.value}")
    typer.echo(f"Parent: {task.parent_task_id or 'none'}")
    typer.echo(f"Input: {_json(task.input_data)}")
    typer.echo("Audit events:")
    for event in runtime.task_events(workspace, task_id):
        transition = ""
        if event.from_status is not None:
            transition = f" {event.from_status.value}->{event.to_status.value}"
        typer.echo(
            f"- {event.occurred_at.isoformat()} | {event.event_type}{transition} | "
            f"{_json(event.details)}"
        )


@app.command("recommendations")
@guarded
def recommendations(
    workspace: str = typer.Option(DEFAULT_WORKSPACE_ID, "--workspace", "-w"),
    database: Path = typer.Option(DEFAULT_DATABASE_PATH, "--database", "-d"),
) -> None:
    runtime = _runtime(database)
    _require_workspace(runtime, workspace)
    typer.echo(f"Recommendations and evidence — workspace {workspace}")
    found = False
    for task in runtime.repositories.tasks.list(workspace):
        for result in runtime.repositories.results.list_for_task(
            workspace, task.task_id
        ):
            if not result.recommendations and not result.evidence:
                continue
            found = True
            _display_result(result)
    if not found:
        typer.echo("No recommendations or evidence recorded.")


@approval_app.command("list")
@guarded
def approval_list(
    workspace: str = typer.Option(DEFAULT_WORKSPACE_ID, "--workspace", "-w"),
    database: Path = typer.Option(DEFAULT_DATABASE_PATH, "--database", "-d"),
) -> None:
    runtime = _runtime(database)
    _require_workspace(runtime, workspace)
    pending = runtime.approvals.list_pending_approvals(workspace)
    typer.echo(f"Pending approvals — workspace {workspace}")
    if not pending:
        typer.echo("No pending approvals.")
    for view in pending:
        typer.echo(
            f"- {view.approval.approval_id} | {view.proposal.action_type} | "
            f"risk={view.policy.risk.value} | expires={view.expires_at.isoformat()} | "
            f"payload_hash={view.approval.payload_hash}"
        )


@approval_app.command("show")
@guarded
def approval_show(
    approval_id: str = typer.Argument(...),
    workspace: str = typer.Option(DEFAULT_WORKSPACE_ID, "--workspace", "-w"),
    database: Path = typer.Option(DEFAULT_DATABASE_PATH, "--database", "-d"),
) -> None:
    runtime = _runtime(database)
    approval = runtime.require_approval(workspace, approval_id)
    proposal = runtime.repositories.proposals.get(workspace, approval.proposal_id)
    if proposal is None:
        raise RecordNotFoundError(
            f"proposal not found for approval: {approval.proposal_id}"
        )
    typer.echo(f"Approval: {approval.approval_id}")
    typer.echo(f"Workspace: {approval.workspace_id}")
    typer.echo(f"Task: {approval.task_id}")
    typer.echo(f"Status: {approval.status.value}")
    typer.echo(f"Action: {proposal.action_type}")
    typer.echo(f"Summary: {proposal.summary}")
    typer.echo(f"Payload hash: {approval.payload_hash}")
    typer.echo(f"Exact payload: {_json(proposal.payload)}")
    typer.echo("Execution mode: SIMULATION ONLY")


@approval_app.command("approve")
@guarded
def approval_approve(
    approval_id: str = typer.Argument(...),
    workspace: str = typer.Option(DEFAULT_WORKSPACE_ID, "--workspace", "-w"),
    reason: str = typer.Option("Human approved simulation only"),
    simulate: bool = typer.Option(
        True, "--simulate/--no-simulate", help="Record a simulation after approval."
    ),
    database: Path = typer.Option(DEFAULT_DATABASE_PATH, "--database", "-d"),
) -> None:
    runtime = _runtime(database)
    current = runtime.require_approval(workspace, approval_id)
    proposal = runtime.repositories.proposals.get(workspace, current.proposal_id)
    if proposal is None:
        raise RecordNotFoundError(
            f"proposal not found for approval: {current.proposal_id}"
        )
    approved = runtime.approvals.approve(
        workspace,
        approval_id,
        actor_id="human_owner",
        reason=reason,
    )
    typer.echo(f"Approval state: {approved.status.value}")
    if simulate:
        execution = runtime.approvals.simulate_execution(
            proposal, approval_id=approved.approval_id
        )
        typer.echo(f"Execution: {execution.status.upper()} ONLY")
        typer.echo("No external action was performed.")


@approval_app.command("reject")
@guarded
def approval_reject(
    approval_id: str = typer.Argument(...),
    workspace: str = typer.Option(DEFAULT_WORKSPACE_ID, "--workspace", "-w"),
    reason: str = typer.Option("Human rejected the proposed action"),
    database: Path = typer.Option(DEFAULT_DATABASE_PATH, "--database", "-d"),
) -> None:
    runtime = _runtime(database)
    runtime.require_approval(workspace, approval_id)
    rejected = runtime.approvals.reject(
        workspace,
        approval_id,
        actor_id="human_owner",
        reason=reason,
    )
    typer.echo(f"Approval state: {rejected.status.value}")
    typer.echo("Execution blocked; no external action was performed.")


@decision_app.command("list")
@guarded
def decision_list(
    workspace: str = typer.Option(DEFAULT_WORKSPACE_ID, "--workspace", "-w"),
    database: Path = typer.Option(DEFAULT_DATABASE_PATH, "--database", "-d"),
) -> None:
    runtime = _runtime(database)
    _require_workspace(runtime, workspace)
    decisions = runtime.repositories.decisions.list(workspace)
    typer.echo(f"Decisions — workspace {workspace}")
    if not decisions:
        typer.echo("No decisions recorded.")
    for decision in decisions:
        typer.echo(
            f"- {decision.decision_id} | {decision.decision_type} | "
            f"{decision.summary}"
        )
        typer.echo(f"  Rationale: {decision.rationale}")


@app.command("demo")
@guarded
def demo(
    seed: str = typer.Option("funny dog owner t-shirt", help="Demo research seed."),
    approval: str = typer.Option(
        "pending",
        "--approval",
        help="Risky demo action: none, pending, approve, or reject.",
    ),
    output: Path | None = typer.Option(None, help="Report output directory."),
    database: Path = typer.Option(DEFAULT_DATABASE_PATH, "--database", "-d"),
) -> None:
    runtime = _runtime(database)
    outcome = runtime.run_demo(
        seed=seed,
        report_output_location=str(output) if output else None,
        approval_choice=approval.lower(),
    )
    typer.echo("KAIOS PHASE 1 — OFFLINE DEMONSTRATION")
    typer.echo("Evidence mode: MOCK / OFFLINE DEMO — NOT LIVE ETSY RESEARCH")
    _display_briefing(runtime, outcome.response)
    _display_demo_approval(outcome)


def _display_briefing(runtime: KAIOSRuntime, response: CEOResponse) -> None:
    parent = runtime.require_task(response.workspace_id, response.parent_task_id)
    child = runtime.require_task(response.workspace_id, response.child_task_id)
    specialist = (
        runtime.repositories.results.get(
            response.workspace_id, response.specialist_result_id
        )
        if response.specialist_result_id
        else None
    )
    typer.echo("CEO briefing")
    typer.echo(f"Workspace: {response.workspace_id}")
    typer.echo(f"Parent CEO task: {parent.task_id} | {parent.status.value}")
    typer.echo(f"Child specialist task: {child.task_id} | {child.status.value}")
    typer.echo(f"Summary: {response.summary}")
    typer.echo(f"Decision recorded: {response.decision_id or 'none'}")
    if specialist is not None:
        _display_result(specialist)


def _display_result(result) -> None:
    typer.echo(f"Result: {result.result_id} | {result.status.value}")
    typer.echo(f"Model provider: {result.data.get('provider', 'not recorded')}")
    typer.echo(f"Metrics: {_json(result.data.get('metrics', {}))}")
    reports = result.data.get("reports", {})
    typer.echo(f"Report paths: {_json(reports)}")
    typer.echo("Evidence:")
    for item in result.evidence:
        label = _evidence_label(item.get("source_type"))
        typer.echo(
            f"- [{label}] {item.get('title', 'Untitled')} | "
            f"{item.get('source', 'unknown source')} | {item.get('url', 'no URL')}"
        )
    typer.echo("Product recommendations:")
    for item in result.recommendations:
        typer.echo(
            f"- {item.get('title', 'Untitled')} | "
            f"recommended={item.get('recommended', False)} | "
            f"confidence={item.get('confidence', 'unknown')} | "
            f"{item.get('why_recommended', '')}"
        )


def _display_demo_approval(outcome: DemoOutcome) -> None:
    if outcome.proposal_review is None:
        typer.echo("Risky action proposal: not created")
        return
    review = outcome.proposal_review
    typer.echo("Risky action proposal")
    typer.echo(f"Proposal: {review.proposal.proposal_id}")
    typer.echo(f"Action: {review.proposal.action_type}")
    typer.echo(f"Policy: {review.policy.outcome.value}")
    typer.echo(f"Payload hash: {review.proposal.approval_payload_hash()}")
    if review.approval is not None:
        status = (
            outcome.resolved_approval.status
            if outcome.resolved_approval is not None
            else review.approval.status
        )
        typer.echo(f"Approval: {review.approval.approval_id} | {status.value}")
    if outcome.simulated_execution is not None:
        typer.echo("Execution: SIMULATED ONLY")
        typer.echo("No external action was performed.")
    elif outcome.resolved_approval is not None:
        typer.echo("Execution: BLOCKED")
    else:
        typer.echo("Execution: BLOCKED UNTIL EXPLICIT HUMAN APPROVAL")


def _evidence_label(source_type: Any) -> str:
    normalized = str(source_type or "").strip().upper()
    if normalized == OFFLINE_DEMO_SOURCE_TYPE:
        return OFFLINE_DEMO_SOURCE_TYPE
    if normalized in {"LIVE", "OFFICIAL_API"}:
        return "LIVE"
    return "FALLBACK"


def _require_workspace(runtime: KAIOSRuntime, workspace_id: str) -> None:
    if runtime.repositories.workspaces.get(workspace_id) is None:
        raise RecordNotFoundError(f"workspace not found: {workspace_id}")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
