from __future__ import annotations

import re

from typer.testing import CliRunner

from kaios.main import app


runner = CliRunner()


def invoke(database_path, *arguments):
    return runner.invoke(app, [*arguments, "--database", str(database_path)])


def test_demo_runs_without_api_keys_and_labels_mock_evidence(monkeypatch, tmp_path):
    monkeypatch.delenv("ETSY_API_KEY", raising=False)
    database_path = tmp_path / "kaios.db"
    result = invoke(
        database_path,
        "demo",
        "--approval",
        "pending",
        "--output",
        str(tmp_path / "reports"),
    )

    assert result.exit_code == 0, result.output
    assert "KAIOS PHASE 1 — OFFLINE DEMONSTRATION" in result.output
    assert "MOCK / OFFLINE DEMO — NOT LIVE ETSY RESEARCH" in result.output
    assert "Model provider: rules" in result.output
    assert "Parent CEO task:" in result.output
    assert "Child specialist task:" in result.output
    assert "Product recommendations:" in result.output
    assert "Decision recorded:" in result.output
    assert "Execution: BLOCKED UNTIL EXPLICIT HUMAN APPROVAL" in result.output


def test_cli_persisted_approval_can_be_viewed_and_approved_once(tmp_path):
    database_path = tmp_path / "kaios.db"
    demo = invoke(
        database_path,
        "demo",
        "--approval",
        "pending",
        "--output",
        str(tmp_path / "reports"),
    )
    approval_id = re.search(r"Approval: (approval_[a-f0-9]+)", demo.output).group(1)

    pending = invoke(database_path, "approval", "list")
    shown = invoke(database_path, "approval", "show", approval_id)
    approved = invoke(database_path, "approval", "approve", approval_id)
    repeated = invoke(database_path, "approval", "approve", approval_id)

    assert approval_id in pending.output
    assert "Exact payload:" in shown.output
    assert "Execution mode: SIMULATION ONLY" in shown.output
    assert approved.exit_code == 0, approved.output
    assert "Approval state: approved" in approved.output
    assert "Execution: SIMULATED ONLY" in approved.output
    assert "No external action was performed." in approved.output
    assert repeated.exit_code == 1
    assert "already been resolved" in repeated.output


def test_cli_rejection_blocks_later_approval(tmp_path):
    database_path = tmp_path / "kaios.db"
    demo = invoke(
        database_path,
        "demo",
        "--approval",
        "pending",
        "--output",
        str(tmp_path / "reports"),
    )
    approval_id = re.search(r"Approval: (approval_[a-f0-9]+)", demo.output).group(1)

    rejected = invoke(database_path, "approval", "reject", approval_id)
    later_approval = invoke(database_path, "approval", "approve", approval_id)

    assert rejected.exit_code == 0, rejected.output
    assert "Approval state: rejected" in rejected.output
    assert "Execution blocked" in rejected.output
    assert later_approval.exit_code == 1
    assert "already been resolved" in later_approval.output


def test_workspace_task_recommendation_and_decision_commands_use_persisted_data(
    tmp_path,
):
    database_path = tmp_path / "kaios.db"
    demo = invoke(
        database_path,
        "demo",
        "--approval",
        "none",
        "--output",
        str(tmp_path / "reports"),
    )
    child_task_id = re.search(
        r"Child specialist task: (task_[a-f0-9]+)", demo.output
    ).group(1)

    workspaces = invoke(database_path, "workspace", "list")
    tasks = invoke(database_path, "task", "list")
    task = invoke(database_path, "task", "show", child_task_id)
    recommendations = invoke(database_path, "recommendations")
    decisions = invoke(database_path, "decision", "list")

    assert "print-on-demand | active" in workspaces.output
    assert "product_intelligence | product_research" in tasks.output
    assert "Audit events:" in task.output
    assert "task_status_changed" in task.output
    assert "MOCK / OFFLINE DEMO" in recommendations.output
    assert "product_opportunity_recommendation" in decisions.output


def test_workspace_creation_and_clear_scope_errors(tmp_path):
    database_path = tmp_path / "kaios.db"
    created = invoke(
        database_path,
        "workspace",
        "create",
        "trading",
        "--name",
        "Trading Intelligence",
    )
    missing = invoke(
        database_path,
        "task",
        "show",
        "does-not-exist",
        "--workspace",
        "trading",
    )

    assert created.exit_code == 0
    assert "Created workspace: trading" in created.output
    assert missing.exit_code == 1
    assert "task not found in workspace trading" in missing.output


def test_cross_workspace_access_is_reported_clearly(tmp_path):
    database_path = tmp_path / "kaios.db"
    demo = invoke(
        database_path,
        "demo",
        "--approval",
        "none",
        "--output",
        str(tmp_path / "reports"),
    )
    task_id = re.search(r"Parent CEO task: (task_[a-f0-9]+)", demo.output).group(1)
    invoke(
        database_path,
        "workspace",
        "create",
        "trading",
        "--name",
        "Trading Intelligence",
    )

    result = invoke(
        database_path,
        "task",
        "show",
        task_id,
        "--workspace",
        "trading",
    )

    assert result.exit_code == 1
    assert "belongs to workspace print-on-demand, not trading" in result.output


def test_database_failure_is_reported_as_cli_error(tmp_path):
    directory_instead_of_database = tmp_path / "database-directory"
    directory_instead_of_database.mkdir()

    result = invoke(directory_instead_of_database, "workspace", "list")

    assert result.exit_code == 1
    assert "Error:" in result.output
