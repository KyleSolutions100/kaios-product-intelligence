"""Temporary in-memory repository implementations for development and tests."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from typing import Iterator, TypeVar

from pydantic import BaseModel

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
from kaios.core.lifecycle import validate_transition
from kaios.core.workspaces import (
    RelationshipValidationError,
    validate_approval_for_proposal,
    validate_decision_context,
    validate_event_for_task,
    validate_proposal_for_task,
    validate_result_for_task,
    validate_task_relationship,
)

from .interfaces import (
    ActionProposalRepository,
    ApprovalRepository,
    DecisionRepository,
    DuplicateRecordError,
    EventRepository,
    ImmutableRecordError,
    RecordNotFoundError,
    ResultRepository,
    TaskRepository,
    WorkspaceRepository,
)


ModelRecord = TypeVar("ModelRecord", bound=BaseModel)


def _copy(record: ModelRecord) -> ModelRecord:
    """Prevent callers from mutating the repository's stored nested values."""

    return record.model_copy(deep=True)


def _scoped_key(workspace_id: str, record_id: str) -> tuple[str, str]:
    if not workspace_id:
        raise ValueError("workspace_id is required")
    if not record_id:
        raise ValueError("record ID is required")
    return workspace_id, record_id


class MemoryWorkspaceRepository(WorkspaceRepository):
    def __init__(self) -> None:
        self._records: dict[str, Workspace] = {}

    def add(self, workspace: Workspace) -> Workspace:
        if workspace.workspace_id in self._records:
            raise DuplicateRecordError(
                f"workspace already exists: {workspace.workspace_id}"
            )
        self._records[workspace.workspace_id] = _copy(workspace)
        return _copy(workspace)

    def get(self, workspace_id: str) -> Workspace | None:
        if not workspace_id:
            raise ValueError("workspace_id is required")
        record = self._records.get(workspace_id)
        return _copy(record) if record is not None else None

    def list(self) -> list[Workspace]:
        return [_copy(self._records[key]) for key in sorted(self._records)]

    def update(self, workspace: Workspace) -> Workspace:
        if workspace.workspace_id not in self._records:
            raise RecordNotFoundError(f"workspace not found: {workspace.workspace_id}")
        self._records[workspace.workspace_id] = _copy(workspace)
        return _copy(workspace)


class MemoryTaskRepository(TaskRepository):
    def __init__(self, workspaces: WorkspaceRepository) -> None:
        self._workspaces = workspaces
        self._records: dict[tuple[str, str], AgentTask] = {}

    def add(self, task: AgentTask) -> AgentTask:
        self._require_workspace(task.workspace_id)
        key = _scoped_key(task.workspace_id, task.task_id)
        if key in self._records:
            raise DuplicateRecordError(
                f"task already exists in workspace {task.workspace_id}: {task.task_id}"
            )
        if task.parent_task_id is not None:
            parent = self.get(task.workspace_id, task.parent_task_id)
            if parent is None:
                raise RecordNotFoundError(
                    "parent task not found in the child's workspace: "
                    f"{task.parent_task_id}"
                )
            validate_task_relationship(parent, task)
        self._records[key] = _copy(task)
        return _copy(task)

    def get(self, workspace_id: str, task_id: str) -> AgentTask | None:
        record = self._records.get(_scoped_key(workspace_id, task_id))
        return _copy(record) if record is not None else None

    def list(
        self, workspace_id: str, *, status: TaskStatus | None = None
    ) -> list[AgentTask]:
        if not workspace_id:
            raise ValueError("workspace_id is required")
        records = [
            record
            for (record_workspace_id, _), record in self._records.items()
            if record_workspace_id == workspace_id
            and (status is None or record.status is status)
        ]
        return [_copy(record) for record in sorted(records, key=_task_sort_key)]

    def update(self, task: AgentTask) -> AgentTask:
        key = _scoped_key(task.workspace_id, task.task_id)
        current = self._records.get(key)
        if current is None:
            raise RecordNotFoundError(
                f"task not found in workspace {task.workspace_id}: {task.task_id}"
            )
        if task.updated_at < current.updated_at:
            raise ValueError("task.updated_at cannot move backwards")
        if task.status is not current.status:
            validate_transition(current.status, task.status)
        if task.parent_task_id != current.parent_task_id:
            raise ImmutableRecordError("task parent relationship cannot be changed")
        self._records[key] = _copy(task)
        return _copy(task)

    def _require_workspace(self, workspace_id: str) -> None:
        if self._workspaces.get(workspace_id) is None:
            raise RecordNotFoundError(f"workspace not found: {workspace_id}")


class MemoryResultRepository(ResultRepository):
    def __init__(self, tasks: TaskRepository) -> None:
        self._tasks = tasks
        self._records: dict[tuple[str, str], AgentResult] = {}

    def add(self, result: AgentResult) -> AgentResult:
        task = self._tasks.get(result.workspace_id, result.task_id)
        if task is None:
            raise RecordNotFoundError(
                "result task not found in the result's workspace: "
                f"{result.task_id}"
            )
        validate_result_for_task(task, result)
        key = _scoped_key(result.workspace_id, result.result_id)
        if key in self._records:
            raise DuplicateRecordError(
                f"result already exists in workspace {result.workspace_id}: "
                f"{result.result_id}"
            )
        self._records[key] = _copy(result)
        return _copy(result)

    def get(self, workspace_id: str, result_id: str) -> AgentResult | None:
        record = self._records.get(_scoped_key(workspace_id, result_id))
        return _copy(record) if record is not None else None

    def list_for_task(self, workspace_id: str, task_id: str) -> list[AgentResult]:
        _scoped_key(workspace_id, task_id)
        records = [
            record
            for (record_workspace_id, _), record in self._records.items()
            if record_workspace_id == workspace_id and record.task_id == task_id
        ]
        return [_copy(record) for record in sorted(records, key=_result_sort_key)]


class MemoryActionProposalRepository(ActionProposalRepository):
    def __init__(self, tasks: TaskRepository) -> None:
        self._tasks = tasks
        self._records: dict[tuple[str, str], ActionProposal] = {}

    def add(self, proposal: ActionProposal) -> ActionProposal:
        task = self._tasks.get(proposal.workspace_id, proposal.task_id)
        if task is None:
            raise RecordNotFoundError(
                "proposal task not found in the proposal's workspace: "
                f"{proposal.task_id}"
            )
        validate_proposal_for_task(task, proposal)
        if proposal.proposed_by_agent != task.assigned_agent:
            raise RelationshipValidationError(
                "proposal.proposed_by_agent must match the task's assigned_agent"
            )
        key = _scoped_key(proposal.workspace_id, proposal.proposal_id)
        if key in self._records:
            raise DuplicateRecordError(
                f"proposal already exists in workspace {proposal.workspace_id}: "
                f"{proposal.proposal_id}"
            )
        self._records[key] = _copy(proposal)
        return _copy(proposal)

    def get(self, workspace_id: str, proposal_id: str) -> ActionProposal | None:
        record = self._records.get(_scoped_key(workspace_id, proposal_id))
        return _copy(record) if record is not None else None

    def list(
        self, workspace_id: str, *, task_id: str | None = None
    ) -> list[ActionProposal]:
        if not workspace_id:
            raise ValueError("workspace_id is required")
        records = [
            record
            for (record_workspace_id, _), record in self._records.items()
            if record_workspace_id == workspace_id
            and (task_id is None or record.task_id == task_id)
        ]
        return [_copy(record) for record in sorted(records, key=_proposal_sort_key)]


class MemoryApprovalRepository(ApprovalRepository):
    def __init__(self, tasks: TaskRepository) -> None:
        self._tasks = tasks
        self._records: dict[tuple[str, str], ApprovalRequest] = {}

    def add(
        self, approval: ApprovalRequest, *, proposal: ActionProposal
    ) -> ApprovalRequest:
        task = self._tasks.get(approval.workspace_id, approval.task_id)
        if task is None:
            raise RecordNotFoundError(
                "approval task not found in the approval's workspace: "
                f"{approval.task_id}"
            )
        validate_approval_for_proposal(proposal, approval)
        if proposal.task_id != task.task_id:
            raise RelationshipValidationError(
                "proposal.task_id does not match the stored task"
            )
        key = _scoped_key(approval.workspace_id, approval.approval_id)
        if key in self._records:
            raise DuplicateRecordError(
                f"approval already exists in workspace {approval.workspace_id}: "
                f"{approval.approval_id}"
            )
        self._records[key] = _copy(approval)
        return _copy(approval)

    def get(self, workspace_id: str, approval_id: str) -> ApprovalRequest | None:
        record = self._records.get(_scoped_key(workspace_id, approval_id))
        return _copy(record) if record is not None else None

    def list(
        self, workspace_id: str, *, status: ApprovalStatus | None = None
    ) -> list[ApprovalRequest]:
        if not workspace_id:
            raise ValueError("workspace_id is required")
        records = [
            record
            for (record_workspace_id, _), record in self._records.items()
            if record_workspace_id == workspace_id
            and (status is None or record.status is status)
        ]
        return [_copy(record) for record in sorted(records, key=_approval_sort_key)]

    def update(self, approval: ApprovalRequest) -> ApprovalRequest:
        key = _scoped_key(approval.workspace_id, approval.approval_id)
        current = self._records.get(key)
        if current is None:
            raise RecordNotFoundError(
                f"approval not found in workspace {approval.workspace_id}: "
                f"{approval.approval_id}"
            )
        identity_fields = (
            "workspace_id",
            "task_id",
            "proposal_id",
            "payload_hash",
            "requested_at",
        )
        if any(
            getattr(current, field) != getattr(approval, field)
            for field in identity_fields
        ):
            raise ImmutableRecordError(
                "approval workspace, relationships and payload hash are immutable"
            )
        if current.status is not ApprovalStatus.PENDING and approval != current:
            raise ImmutableRecordError("a resolved approval cannot be changed")
        self._records[key] = _copy(approval)
        return _copy(approval)


class MemoryDecisionRepository(DecisionRepository):
    def __init__(
        self,
        workspaces: WorkspaceRepository,
        tasks: TaskRepository,
        approvals: ApprovalRepository,
    ) -> None:
        self._workspaces = workspaces
        self._tasks = tasks
        self._approvals = approvals
        self._records: dict[tuple[str, str], DecisionRecord] = {}

    def add(self, decision: DecisionRecord) -> DecisionRecord:
        if self._workspaces.get(decision.workspace_id) is None:
            raise RecordNotFoundError(f"workspace not found: {decision.workspace_id}")
        task = None
        if decision.related_task_id is not None:
            task = self._tasks.get(decision.workspace_id, decision.related_task_id)
            if task is None:
                raise RecordNotFoundError(
                    "decision task not found in the decision's workspace: "
                    f"{decision.related_task_id}"
                )
        approval = None
        if decision.related_approval_id is not None:
            approval = self._approvals.get(
                decision.workspace_id, decision.related_approval_id
            )
            if approval is None:
                raise RecordNotFoundError(
                    "decision approval not found in the decision's workspace: "
                    f"{decision.related_approval_id}"
                )
        validate_decision_context(decision, task=task, approval=approval)
        key = _scoped_key(decision.workspace_id, decision.decision_id)
        if key in self._records:
            raise DuplicateRecordError(
                f"decision already exists in workspace {decision.workspace_id}: "
                f"{decision.decision_id}"
            )
        self._records[key] = _copy(decision)
        return _copy(decision)

    def get(self, workspace_id: str, decision_id: str) -> DecisionRecord | None:
        record = self._records.get(_scoped_key(workspace_id, decision_id))
        return _copy(record) if record is not None else None

    def list(self, workspace_id: str) -> list[DecisionRecord]:
        if not workspace_id:
            raise ValueError("workspace_id is required")
        records = [
            record
            for (record_workspace_id, _), record in self._records.items()
            if record_workspace_id == workspace_id
        ]
        return [_copy(record) for record in sorted(records, key=_decision_sort_key)]


class MemoryEventRepository(EventRepository):
    def __init__(self, tasks: TaskRepository) -> None:
        self._tasks = tasks
        self._records: dict[tuple[str, str], TaskEvent] = {}

    def add(self, event: TaskEvent) -> TaskEvent:
        task = self._tasks.get(event.workspace_id, event.task_id)
        if task is None:
            raise RecordNotFoundError(
                "event task not found in the event's workspace: " f"{event.task_id}"
            )
        validate_event_for_task(task, event)
        key = _scoped_key(event.workspace_id, event.event_id)
        if key in self._records:
            raise DuplicateRecordError(
                f"event already exists in workspace {event.workspace_id}: "
                f"{event.event_id}"
            )
        self._records[key] = _copy(event)
        return _copy(event)

    def get(self, workspace_id: str, event_id: str) -> TaskEvent | None:
        record = self._records.get(_scoped_key(workspace_id, event_id))
        return _copy(record) if record is not None else None

    def list_for_task(self, workspace_id: str, task_id: str) -> list[TaskEvent]:
        _scoped_key(workspace_id, task_id)
        records = [
            record
            for (record_workspace_id, _), record in self._records.items()
            if record_workspace_id == workspace_id and record.task_id == task_id
        ]
        return [_copy(record) for record in sorted(records, key=_event_sort_key)]


class InMemoryRepositories:
    """Convenience container with correctly connected in-memory repositories."""

    def __init__(self) -> None:
        self.workspaces = MemoryWorkspaceRepository()
        self.tasks = MemoryTaskRepository(self.workspaces)
        self.results = MemoryResultRepository(self.tasks)
        self.proposals = MemoryActionProposalRepository(self.tasks)
        self.approvals = MemoryApprovalRepository(self.tasks)
        self.decisions = MemoryDecisionRepository(
            self.workspaces, self.tasks, self.approvals
        )
        self.events = MemoryEventRepository(self.tasks)

    @contextmanager
    def transaction(self) -> Iterator[InMemoryRepositories]:
        """Roll back all in-memory repositories if a coordinated write fails."""

        repositories = (
            self.workspaces,
            self.tasks,
            self.results,
            self.proposals,
            self.approvals,
            self.decisions,
            self.events,
        )
        snapshots = [deepcopy(repository._records) for repository in repositories]
        try:
            yield self
        except BaseException:
            for repository, snapshot in zip(repositories, snapshots):
                repository._records = snapshot
            raise


def _task_sort_key(task: AgentTask) -> tuple:
    return task.created_at, task.task_id


def _result_sort_key(result: AgentResult) -> tuple:
    return result.created_at, result.result_id


def _approval_sort_key(approval: ApprovalRequest) -> tuple:
    return approval.requested_at, approval.approval_id


def _proposal_sort_key(proposal: ActionProposal) -> tuple:
    return proposal.created_at, proposal.proposal_id


def _decision_sort_key(decision: DecisionRecord) -> tuple:
    return decision.created_at, decision.decision_id


def _event_sort_key(event: TaskEvent) -> tuple:
    return event.occurred_at, event.event_id
