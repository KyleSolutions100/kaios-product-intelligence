"""Coordinated lifecycle and persistence boundary for agent task execution."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol

from kaios.core.contracts import (
    AgentResult,
    AgentTask,
    ResultStatus,
    TaskStatus,
)
from kaios.core.lifecycle import transition_task
from kaios.repositories.interfaces import (
    EventRepository,
    RecordNotFoundError,
    ResultRepository,
    TaskRepository,
)

from .registry import AgentRegistry


class AgentRepositoryUnitOfWork(Protocol):
    tasks: TaskRepository
    results: ResultRepository
    events: EventRepository

    def transaction(self) -> AbstractContextManager[Any]:
        """Return a rollback-capable repository transaction."""


class TaskExecutionError(RuntimeError):
    """Raised when a persisted task is not executable in its current state."""


class AgentTaskService:
    def __init__(
        self, registry: AgentRegistry, repositories: AgentRepositoryUnitOfWork
    ) -> None:
        self._registry = registry
        self._repositories = repositories

    def execute(self, workspace_id: str, task_id: str) -> AgentResult:
        running_task = self._start_task(workspace_id, task_id)
        try:
            result = self._registry.route(running_task, workspace_id=workspace_id)
        except Exception as error:
            result = AgentResult.for_task(
                running_task,
                agent_id=running_task.assigned_agent,
                status=ResultStatus.FAILED,
                summary="Agent execution failed unexpectedly",
                error=f"{type(error).__name__}: {error}",
                data={"failed_stage": "agent_execution"},
            )
        return self._complete_task(running_task, result)

    def _start_task(self, workspace_id: str, task_id: str) -> AgentTask:
        with self._repositories.transaction():
            task = self._repositories.tasks.get(workspace_id, task_id)
            if task is None:
                raise RecordNotFoundError(
                    f"task not found in workspace {workspace_id}: {task_id}"
                )
            self._registry.resolve(task, workspace_id=workspace_id)
            if task.status is TaskStatus.CREATED:
                task = self._transition(
                    task, TaskStatus.QUEUED, reason="task accepted for routing"
                )
            if task.status is TaskStatus.QUEUED:
                task = self._transition(
                    task, TaskStatus.RUNNING, reason="agent execution started"
                )
            if task.status is not TaskStatus.RUNNING:
                raise TaskExecutionError(
                    f"task cannot execute from status {task.status.value}"
                )
            return task

    def _complete_task(
        self, running_task: AgentTask, result: AgentResult
    ) -> AgentResult:
        with self._repositories.transaction():
            current = self._repositories.tasks.get(
                running_task.workspace_id, running_task.task_id
            )
            if current is None:
                raise RecordNotFoundError(
                    "task disappeared before completion: " f"{running_task.task_id}"
                )
            if current.status is not TaskStatus.RUNNING:
                raise TaskExecutionError(
                    f"task completion requires running status, not {current.status.value}"
                )
            stored_result = self._repositories.results.add(result)
            target_status = (
                TaskStatus.FAILED
                if result.status is ResultStatus.FAILED
                else TaskStatus.SUCCEEDED
            )
            self._transition(
                current,
                target_status,
                reason="agent execution completed",
                details={
                    "result_id": stored_result.result_id,
                    "result_status": stored_result.status.value,
                },
            )
            return stored_result

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
