from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from kaios.approvals import ApprovalStateError, ExecutionBlockedError
from kaios.core.contracts import ApprovalStatus, TaskStatus
from kaios.model_providers.litellm import LiteLLMModelProvider
from kaios.offline import OFFLINE_DEMO_SOURCE_TYPE
from kaios.runtime import build_runtime
from kaios.repositories.sqlite import SQLiteRepositories


def test_offline_demo_persists_complete_flow_and_survives_restart(tmp_path):
    database_path = tmp_path / "kaios.db"
    reports = tmp_path / "reports"
    runtime = build_runtime(database_path)

    outcome = runtime.run_demo(
        seed="book lover sweatshirt",
        report_output_location=str(reports),
        approval_choice="approve",
    )

    assert outcome.response.status.value == "succeeded"
    assert outcome.resolved_approval.status is ApprovalStatus.APPROVED
    assert outcome.simulated_execution.status == "simulated"
    assert outcome.simulated_execution.note.endswith("no external action was performed.")
    tasks = runtime.repositories.tasks.list("print-on-demand")
    assert len(tasks) == 3
    assert {task.status for task in tasks} == {TaskStatus.SUCCEEDED, TaskStatus.QUEUED}
    results = [
        result
        for task in tasks
        for result in runtime.repositories.results.list_for_task(
            "print-on-demand", task.task_id
        )
    ]
    assert len(results) == 2
    assert len(runtime.repositories.decisions.list("print-on-demand")) == 1
    assert len(runtime.repositories.approvals.list("print-on-demand")) == 1
    specialist = runtime.repositories.results.get(
        "print-on-demand", outcome.response.specialist_result_id
    )
    assert specialist.data["provider"] == "rules"
    assert specialist.data["metrics"]["evidence_count"] == 3
    assert specialist.recommendations[0]["recommended"] is True
    assert {
        item["source_type"] for item in specialist.evidence
    } == {OFFLINE_DEMO_SOURCE_TYPE}
    assert Path(specialist.data["reports"]["markdown"]).exists()
    assert Path(specialist.data["reports"]["json"]).exists()
    assert "MOCK / OFFLINE DEMO" in Path(
        specialist.data["reports"]["markdown"]
    ).read_text(encoding="utf-8")
    json_report = json.loads(
        Path(specialist.data["reports"]["json"]).read_text(encoding="utf-8")
    )
    assert json_report["evidence_mode"] == OFFLINE_DEMO_SOURCE_TYPE
    assert "not live marketplace research" in json_report["disclaimer"]

    restarted = SQLiteRepositories(database_path)
    assert len(restarted.tasks.list("print-on-demand")) == 3
    assert restarted.approvals.get(
        "print-on-demand", outcome.resolved_approval.approval_id
    ) == outcome.resolved_approval
    action_task_id = outcome.proposal_review.proposal.task_id
    assert "simulated_execution_completed" in {
        event.event_type
        for event in restarted.events.list_for_task("print-on-demand", action_task_id)
    }


def test_offline_runtime_never_calls_network_or_paid_provider(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("network or paid model access was attempted")

    monkeypatch.delenv("ETSY_API_KEY", raising=False)
    monkeypatch.setattr("httpx.Client", forbidden)
    monkeypatch.setattr(LiteLLMModelProvider, "generate", forbidden)
    runtime = build_runtime(tmp_path / "kaios.db")

    outcome = runtime.run_demo(
        seed="teacher tote bag",
        report_output_location=str(tmp_path / "reports"),
        approval_choice="none",
    )

    assert outcome.response.status.value == "succeeded"
    result = runtime.repositories.results.get(
        "print-on-demand", outcome.response.specialist_result_id
    )
    assert result.data["provider"] == "rules"


def test_pending_action_is_blocked_until_approval(tmp_path):
    runtime = build_runtime(tmp_path / "kaios.db")
    outcome = runtime.run_demo(
        seed="gardening mug",
        report_output_location=str(tmp_path / "reports"),
        approval_choice="pending",
    )
    approval = outcome.proposal_review.approval

    with pytest.raises(ExecutionBlockedError):
        runtime.approvals.simulate_execution(outcome.proposal_review.proposal)

    approved = runtime.approvals.approve(
        "print-on-demand", approval.approval_id, actor_id="human_owner"
    )
    execution = runtime.approvals.simulate_execution(
        outcome.proposal_review.proposal, approval_id=approved.approval_id
    )

    assert execution.status == "simulated"
    with pytest.raises(ApprovalStateError):
        runtime.approvals.approve(
            "print-on-demand", approval.approval_id, actor_id="human_owner"
        )


def test_rejection_prevents_execution(tmp_path):
    runtime = build_runtime(tmp_path / "kaios.db")
    outcome = runtime.run_demo(
        seed="camping shirt",
        report_output_location=str(tmp_path / "reports"),
        approval_choice="reject",
    )

    assert outcome.resolved_approval.status is ApprovalStatus.REJECTED
    with pytest.raises(ExecutionBlockedError):
        runtime.approvals.simulate_execution(
            outcome.proposal_review.proposal,
            approval_id=outcome.resolved_approval.approval_id,
        )


def test_direct_invalid_demo_choice_has_no_side_effects(tmp_path):
    database_path = tmp_path / "kaios.db"
    report_path = tmp_path / "reports"
    runtime = build_runtime(database_path)

    with pytest.raises(ValueError, match="approval_choice must be one of"):
        runtime.run_demo(
            seed="invalid choice seed",
            report_output_location=str(report_path),
            approval_choice="invalid",
        )

    assert runtime.repositories.workspaces.list() == []
    with runtime.repositories.database.read() as connection:
        for table in (
            "tasks",
            "results",
            "decisions",
            "approvals",
            "action_proposals",
            "task_events",
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert not report_path.exists()


def test_legacy_e2e_script_labels_all_output_as_offline_demo():
    repository_root = Path(__file__).resolve().parent.parent

    completed = subprocess.run(
        [sys.executable, "scripts/e2e.py"],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Evidence mode: MOCK / OFFLINE DEMO" in completed.stdout
    assert "not live marketplace research" in completed.stdout
