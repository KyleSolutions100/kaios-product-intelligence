"""Abstract repositories that keep domain contracts independent of storage."""

from __future__ import annotations

from abc import ABC, abstractmethod

from kaios.core.contracts import (
    ActionProposal,
    AgentResult,
    AgentTask,
    ApprovalRequest,
    ApprovalStatus,
    DecisionRecord,
    TaskEvent,
    TaskStatus,
    Workspace,
)


class RepositoryError(RuntimeError):
    """Base error for persistence-neutral repository failures."""


class DuplicateRecordError(RepositoryError):
    """Raised when a record already exists inside a workspace."""


class RecordNotFoundError(RepositoryError):
    """Raised when a requested record or required relationship is missing."""


class ImmutableRecordError(RepositoryError):
    """Raised when an append-only identity or resolved record is changed."""


class WorkspaceRepository(ABC):
    @abstractmethod
    def add(self, workspace: Workspace) -> Workspace:
        """Create a workspace."""

    @abstractmethod
    def get(self, workspace_id: str) -> Workspace | None:
        """Return one workspace by ID."""

    @abstractmethod
    def list(self) -> list[Workspace]:
        """Return all workspace definitions."""

    @abstractmethod
    def update(self, workspace: Workspace) -> Workspace:
        """Replace an existing workspace definition."""


class TaskRepository(ABC):
    @abstractmethod
    def add(self, task: AgentTask) -> AgentTask:
        """Create a task after validating its workspace and parent."""

    @abstractmethod
    def get(self, workspace_id: str, task_id: str) -> AgentTask | None:
        """Return a task only from the requested workspace."""

    @abstractmethod
    def list(
        self, workspace_id: str, *, status: TaskStatus | None = None
    ) -> list[AgentTask]:
        """Return tasks from one workspace, optionally filtered by status."""

    @abstractmethod
    def update(self, task: AgentTask) -> AgentTask:
        """Update an existing task while enforcing legal status changes."""


class ResultRepository(ABC):
    @abstractmethod
    def add(self, result: AgentResult) -> AgentResult:
        """Store a result after validating its task relationship."""

    @abstractmethod
    def get(self, workspace_id: str, result_id: str) -> AgentResult | None:
        """Return a result only from the requested workspace."""

    @abstractmethod
    def list_for_task(self, workspace_id: str, task_id: str) -> list[AgentResult]:
        """Return results for one workspace-scoped task."""


class ApprovalRepository(ABC):
    @abstractmethod
    def add(
        self, approval: ApprovalRequest, *, proposal: ActionProposal
    ) -> ApprovalRequest:
        """Store an approval bound to the exact supplied action proposal."""

    @abstractmethod
    def get(self, workspace_id: str, approval_id: str) -> ApprovalRequest | None:
        """Return an approval only from the requested workspace."""

    @abstractmethod
    def list(
        self, workspace_id: str, *, status: ApprovalStatus | None = None
    ) -> list[ApprovalRequest]:
        """Return approvals from one workspace, optionally by status."""

    @abstractmethod
    def update(self, approval: ApprovalRequest) -> ApprovalRequest:
        """Resolve a pending approval without changing its identity or payload."""


class DecisionRepository(ABC):
    @abstractmethod
    def add(self, decision: DecisionRecord) -> DecisionRecord:
        """Store a decision after validating its task and approval context."""

    @abstractmethod
    def get(self, workspace_id: str, decision_id: str) -> DecisionRecord | None:
        """Return a decision only from the requested workspace."""

    @abstractmethod
    def list(self, workspace_id: str) -> list[DecisionRecord]:
        """Return decisions from one workspace."""


class EventRepository(ABC):
    @abstractmethod
    def add(self, event: TaskEvent) -> TaskEvent:
        """Append an event after validating its task relationship."""

    @abstractmethod
    def get(self, workspace_id: str, event_id: str) -> TaskEvent | None:
        """Return an event only from the requested workspace."""

    @abstractmethod
    def list_for_task(self, workspace_id: str, task_id: str) -> list[TaskEvent]:
        """Return the event history for one workspace-scoped task."""
