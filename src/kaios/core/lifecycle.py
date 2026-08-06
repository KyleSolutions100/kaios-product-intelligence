"""Legal state transitions for workspace-scoped KAIOS tasks."""

from __future__ import annotations

from datetime import datetime, timezone

from .contracts import AgentTask, TaskEvent, TaskStatus


class InvalidTaskTransition(ValueError):
    """Raised when a task attempts an illegal lifecycle transition."""


ALLOWED_TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.CREATED: frozenset({TaskStatus.QUEUED, TaskStatus.CANCELLED}),
    TaskStatus.QUEUED: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED}),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.WAITING_FOR_APPROVAL,
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.WAITING_FOR_APPROVAL: frozenset(
        {TaskStatus.QUEUED, TaskStatus.CANCELLED}
    ),
    TaskStatus.SUCCEEDED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}


def can_transition(from_status: TaskStatus, to_status: TaskStatus) -> bool:
    return to_status in ALLOWED_TASK_TRANSITIONS[from_status]


def validate_transition(from_status: TaskStatus, to_status: TaskStatus) -> None:
    if not can_transition(from_status, to_status):
        raise InvalidTaskTransition(
            f"illegal task transition: {from_status.value} -> {to_status.value}"
        )


def transition_task(
    task: AgentTask,
    to_status: TaskStatus,
    *,
    occurred_at: datetime | None = None,
    details: dict | None = None,
) -> tuple[AgentTask, TaskEvent]:
    """Return an updated immutable task and its matching audit event."""

    validate_transition(task.status, to_status)
    when = occurred_at or datetime.now(timezone.utc)
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("occurred_at must include timezone information")
    if when < task.updated_at:
        raise ValueError("occurred_at cannot be earlier than task.updated_at")

    task_values = task.model_dump()
    task_values.update(status=to_status, updated_at=when)
    updated_task = AgentTask.model_validate(task_values)
    event = TaskEvent(
        workspace_id=task.workspace_id,
        task_id=task.task_id,
        event_type="task_status_changed",
        from_status=task.status,
        to_status=to_status,
        details=details or {},
        occurred_at=when,
    )
    return updated_task, event
