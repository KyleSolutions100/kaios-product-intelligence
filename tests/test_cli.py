from __future__ import annotations

import re

import httpx
from typer.testing import CliRunner

from kaios.main import app
from kaios.repositories.sqlite import SQLiteRepositories
from kaios.sources import EtsyProvider


runner = CliRunner()


def invoke(database_path, *arguments):
    return runner.invoke(app, [*arguments, "--database", str(database_path)])


def patch_live_etsy(monkeypatch, *, results=None, status=200):
    if results is None:
        results = [
            {
                "listing_id": 700,
                "title": "Public Live Test Listing",
                "url": "https://www.etsy.com/listing/700/public-live-test",
                "price": {"amount": 1500, "divisor": 100, "currency_code": "GBP"},
                "num_favorers": 7,
                "shop": {"shop_id": 8, "shop_name": "PublicTestShop", "review_count": 9},
                "images": [{"url_fullxfull": "https://i.etsystatic.com/public-test.jpg"}],
                "tags": ["public test"],
            }
        ]
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"count": len(results), "results": results})

    provider = EtsyProvider(
        "test-key:test-secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=0,
        sleep=lambda seconds: None,
    )
    monkeypatch.setenv("ETSY_API_KEY", "test-key:test-secret")
    monkeypatch.setattr("kaios.sources.factory.EtsyProvider", lambda api_key: provider)
    return calls


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


def test_invalid_demo_choice_has_no_workflow_or_report_side_effects(tmp_path):
    database_path = tmp_path / "kaios.db"
    report_path = tmp_path / "reports"

    result = invoke(
        database_path,
        "demo",
        "--approval",
        "invalid-choice",
        "--output",
        str(report_path),
    )

    assert result.exit_code == 1
    assert "approval_choice must be one of" in result.output
    repositories = SQLiteRepositories(database_path)
    assert repositories.tasks.list("print-on-demand") == []
    assert repositories.decisions.list("print-on-demand") == []
    assert repositories.approvals.list("print-on-demand") == []
    assert repositories.proposals.list("print-on-demand") == []
    assert not report_path.exists()
    with repositories.database.read() as connection:
        assert connection.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM task_events").fetchone()[0] == 0


def test_research_config_applies_supported_offline_settings(monkeypatch, tmp_path):
    calls = patch_live_etsy(monkeypatch)
    database_path = tmp_path / "kaios.db"
    report_path = tmp_path / "configured-reports"
    config_path = tmp_path / "research.yaml"
    config_path.write_text(
        f"""
marketplace: etsy
default_search_limit: 2
output_dir: {report_path}
model_provider: rules
agent_model_providers:
  product_intelligence: rules
""".strip(),
        encoding="utf-8",
    )

    result = invoke(
        database_path,
        "research",
        "configured seed",
        "--config",
        str(config_path),
    )

    assert result.exit_code == 0, result.output
    assert "[LIVE]" in result.output
    assert len(calls) == 1
    assert "Model provider: rules" in result.output
    repositories = SQLiteRepositories(database_path)
    specialist = next(
        task
        for task in repositories.tasks.list("print-on-demand")
        if task.task_type == "product_research"
    )
    assert specialist.input_data["marketplace"] == "etsy"
    assert specialist.input_data["result_limit"] == 2
    assert specialist.input_data["report_output_location"] == str(report_path)
    assert (report_path / "latest.json").exists()


def test_research_config_cli_options_override_file_values(monkeypatch, tmp_path):
    patch_live_etsy(monkeypatch)
    database_path = tmp_path / "kaios.db"
    config_path = tmp_path / "research.yaml"
    config_path.write_text(
        """
marketplace: configured-marketplace
search_limit: 2
output_dir: configured-reports
model_provider: rules
""".strip(),
        encoding="utf-8",
    )
    override_reports = tmp_path / "override-reports"

    result = invoke(
        database_path,
        "research",
        "override seed",
        "--config",
        str(config_path),
        "--marketplace",
        "etsy",
        "--limit",
        "1",
        "--output",
        str(override_reports),
    )

    assert result.exit_code == 0, result.output
    repositories = SQLiteRepositories(database_path)
    specialist = next(
        task
        for task in repositories.tasks.list("print-on-demand")
        if task.task_type == "product_research"
    )
    assert specialist.input_data["marketplace"] == "etsy"
    assert specialist.input_data["result_limit"] == 1
    assert specialist.input_data["report_output_location"] == str(override_reports)


def test_missing_research_config_fails_before_database_creation(tmp_path):
    database_path = tmp_path / "kaios.db"
    missing_config = tmp_path / "missing.yaml"

    result = invoke(
        database_path,
        "research",
        "missing config seed",
        "--config",
        str(missing_config),
    )

    assert result.exit_code == 1
    assert "configuration file not found" in result.output
    assert not database_path.exists()


def test_invalid_research_config_fails_before_database_creation(tmp_path):
    database_path = tmp_path / "kaios.db"
    invalid_config = tmp_path / "invalid.yaml"
    invalid_config.write_text("model_provider: [", encoding="utf-8")

    result = invoke(
        database_path,
        "research",
        "invalid config seed",
        "--config",
        str(invalid_config),
    )

    assert result.exit_code == 1
    assert "invalid YAML configuration" in result.output
    assert not database_path.exists()


def test_live_research_missing_key_fails_before_database_or_network(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("ETSY_API_KEY", raising=False)
    network_calls = []

    def forbidden_network(*args, **kwargs):
        network_calls.append((args, kwargs))
        raise AssertionError("network should not be reached")

    monkeypatch.setattr("httpx.Client", forbidden_network)
    database_path = tmp_path / "kaios.db"

    result = invoke(database_path, "research", "dog owner shirt")

    assert result.exit_code == 1
    assert "ETSY_API_KEY is required" in result.output
    assert "test-secret" not in result.output
    assert network_calls == []
    assert not database_path.exists()


def test_live_research_source_failure_returns_nonzero_with_audit_trail(
    monkeypatch, tmp_path
):
    patch_live_etsy(monkeypatch, status=403)
    database_path = tmp_path / "kaios.db"

    result = invoke(database_path, "research", "dog owner shirt")

    assert result.exit_code == 1
    assert "MarketplaceAuthenticationError" in result.output
    repositories = SQLiteRepositories(database_path)
    tasks = repositories.tasks.list("print-on-demand")
    assert len(tasks) == 2
    assert {task.status.value for task in tasks} == {"failed"}
    assert repositories.decisions.list("print-on-demand") == []
