from datetime import timedelta

import pytest

from kaios.core.contracts import AgentTask, TaskStatus
from kaios.core.lifecycle import (
    ALLOWED_TASK_TRANSITIONS,
    InvalidTaskTransition,
    can_transition,
    transition_task,
    validate_transition,
)


def make_task(status: TaskStatus = TaskStatus.CREATED) -> AgentTask:
    return AgentTask(
        workspace_id="workspace-a",
        task_type="product_research",
        assigned_agent="product_intelligence",
        status=status,
    )


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [
        (TaskStatus.CREATED, TaskStatus.QUEUED),
        (TaskStatus.CREATED, TaskStatus.CANCELLED),
        (TaskStatus.QUEUED, TaskStatus.RUNNING),
        (TaskStatus.QUEUED, TaskStatus.CANCELLED),
        (TaskStatus.RUNNING, TaskStatus.WAITING_FOR_APPROVAL),
        (TaskStatus.RUNNING, TaskStatus.SUCCEEDED),
        (TaskStatus.RUNNING, TaskStatus.FAILED),
        (TaskStatus.RUNNING, TaskStatus.CANCELLED),
        (TaskStatus.WAITING_FOR_APPROVAL, TaskStatus.QUEUED),
        (TaskStatus.WAITING_FOR_APPROVAL, TaskStatus.CANCELLED),
    ],
)
def test_documented_transitions_are_legal(from_status, to_status):
    assert can_transition(from_status, to_status)
    validate_transition(from_status, to_status)


@pytest.mark.parametrize(
    "terminal_status",
    [TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED],
)
def test_terminal_states_have_no_outbound_transitions(terminal_status):
    assert ALLOWED_TASK_TRANSITIONS[terminal_status] == frozenset()
    with pytest.raises(InvalidTaskTransition):
        validate_transition(terminal_status, TaskStatus.QUEUED)


def test_task_cannot_skip_from_created_to_running():
    with pytest.raises(InvalidTaskTransition, match="created -> running"):
        validate_transition(TaskStatus.CREATED, TaskStatus.RUNNING)


def test_transition_returns_updated_task_and_matching_event():
    task = make_task()
    when = task.updated_at + timedelta(seconds=1)

    updated, event = transition_task(
        task,
        TaskStatus.QUEUED,
        occurred_at=when,
        details={"reason": "accepted by orchestrator"},
    )

    assert task.status is TaskStatus.CREATED
    assert updated.status is TaskStatus.QUEUED
    assert updated.updated_at == when
    assert event.workspace_id == task.workspace_id
    assert event.task_id == task.task_id
    assert event.from_status is TaskStatus.CREATED
    assert event.to_status is TaskStatus.QUEUED
    assert event.occurred_at == when


def test_transition_rejects_time_before_current_task_update():
    task = make_task()

    with pytest.raises(ValueError, match="earlier"):
        transition_task(
            task,
            TaskStatus.QUEUED,
            occurred_at=task.updated_at - timedelta(seconds=1),
        )
