"""SQLite-backed repositories for durable, workspace-scoped KAIOS records."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
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
    validate_result_for_task,
    validate_task_relationship,
)

from .interfaces import (
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


DEFAULT_DATABASE_PATH = Path("data/kaios.db")
CURRENT_SCHEMA_VERSION = 1

ModelRecord = TypeVar("ModelRecord", bound=BaseModel)


def _require_id(value: str, label: str) -> str:
    if not value:
        raise ValueError(f"{label} is required")
    return value


def _serialize(record: BaseModel) -> str:
    """Serialize validated models to deterministic, JSON-compatible text."""

    return json.dumps(
        record.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _reconstruct(model: type[ModelRecord], payload: str) -> ModelRecord:
    """Revalidate database data instead of returning mutable stored objects."""

    return model.model_validate_json(payload)


def _timestamp(value: datetime) -> str:
    """Store timestamps as unambiguous UTC ISO-8601 text."""

    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


class SQLiteDatabase:
    """Own SQLite connection settings, transactions, and schema migrations."""

    def __init__(self, path: str | Path = DEFAULT_DATABASE_PATH) -> None:
        self.path = Path(path)
        self._active_connection: ContextVar[sqlite3.Connection | None] = ContextVar(
            f"kaios_sqlite_connection_{id(self)}", default=None
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        active_connection = self._active_connection.get()
        if active_connection is not None:
            yield active_connection
            return
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Commit an atomic unit of work, or roll it back on any failure."""

        active_connection = self._active_connection.get()
        if active_connection is not None:
            yield active_connection
            return
        connection = self.connect()
        token = self._active_connection.set(connection)
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            self._active_connection.reset(token)
            connection.close()

    def _initialize_schema(self) -> None:
        with self.transaction() as connection:
            database_version = connection.execute("PRAGMA user_version").fetchone()[0]
            if database_version > CURRENT_SCHEMA_VERSION:
                raise RuntimeError(
                    "database schema version is newer than this KAIOS build: "
                    f"{database_version} > {CURRENT_SCHEMA_VERSION}"
                )
            for version in range(database_version + 1, CURRENT_SCHEMA_VERSION + 1):
                _apply_migration(connection, version)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, _timestamp(datetime.now(timezone.utc))),
                )
                connection.execute(f"PRAGMA user_version = {version}")


def _apply_migration(connection: sqlite3.Connection, version: int) -> None:
    migrations = {1: _SCHEMA_VERSION_1}
    statements = migrations.get(version)
    if statements is None:
        raise RuntimeError(f"missing SQLite migration for schema version {version}")
    for statement in statements:
        connection.execute(statement)


_SCHEMA_VERSION_1 = (
    """
    CREATE TABLE schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE workspaces (
        workspace_id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE tasks (
        workspace_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        parent_task_id TEXT,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        PRIMARY KEY (workspace_id, task_id),
        FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id),
        FOREIGN KEY (workspace_id, parent_task_id)
            REFERENCES tasks(workspace_id, task_id)
    )
    """,
    """
    CREATE TABLE results (
        workspace_id TEXT NOT NULL,
        result_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        PRIMARY KEY (workspace_id, result_id),
        FOREIGN KEY (workspace_id, task_id)
            REFERENCES tasks(workspace_id, task_id)
    )
    """,
    """
    CREATE TABLE approvals (
        workspace_id TEXT NOT NULL,
        approval_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        proposal_id TEXT NOT NULL,
        status TEXT NOT NULL,
        requested_at TEXT NOT NULL,
        resolved_at TEXT,
        payload_json TEXT NOT NULL,
        PRIMARY KEY (workspace_id, approval_id),
        FOREIGN KEY (workspace_id, task_id)
            REFERENCES tasks(workspace_id, task_id)
    )
    """,
    """
    CREATE TABLE decisions (
        workspace_id TEXT NOT NULL,
        decision_id TEXT NOT NULL,
        related_task_id TEXT,
        related_approval_id TEXT,
        created_at TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        PRIMARY KEY (workspace_id, decision_id),
        FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id),
        FOREIGN KEY (workspace_id, related_task_id)
            REFERENCES tasks(workspace_id, task_id),
        FOREIGN KEY (workspace_id, related_approval_id)
            REFERENCES approvals(workspace_id, approval_id)
    )
    """,
    """
    CREATE TABLE task_events (
        workspace_id TEXT NOT NULL,
        event_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        PRIMARY KEY (workspace_id, event_id),
        FOREIGN KEY (workspace_id, task_id)
            REFERENCES tasks(workspace_id, task_id)
    )
    """,
    "CREATE INDEX idx_workspaces_status ON workspaces(status, created_at)",
    "CREATE INDEX idx_tasks_status ON tasks(workspace_id, status, created_at, task_id)",
    "CREATE INDEX idx_tasks_parent ON tasks(workspace_id, parent_task_id)",
    "CREATE INDEX idx_results_task ON results(workspace_id, task_id, created_at, result_id)",
    "CREATE INDEX idx_approvals_status ON approvals(workspace_id, status, requested_at, approval_id)",
    "CREATE INDEX idx_approvals_task ON approvals(workspace_id, task_id)",
    "CREATE INDEX idx_decisions_created ON decisions(workspace_id, created_at, decision_id)",
    "CREATE INDEX idx_decisions_task ON decisions(workspace_id, related_task_id)",
    "CREATE INDEX idx_decisions_approval ON decisions(workspace_id, related_approval_id)",
    "CREATE INDEX idx_events_task ON task_events(workspace_id, task_id, occurred_at, event_id)",
)


def _duplicate(message: str, error: sqlite3.IntegrityError) -> None:
    if "UNIQUE constraint failed" in str(error):
        raise DuplicateRecordError(message) from error
    if "FOREIGN KEY constraint failed" in str(error):
        raise RecordNotFoundError("required workspace relationship not found") from error
    raise error


class SQLiteWorkspaceRepository(WorkspaceRepository):
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def add(self, workspace: Workspace) -> Workspace:
        try:
            with self._database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO workspaces(
                        workspace_id, status, created_at, updated_at, payload_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        workspace.workspace_id,
                        workspace.status.value,
                        _timestamp(workspace.created_at),
                        _timestamp(workspace.updated_at),
                        _serialize(workspace),
                    ),
                )
        except sqlite3.IntegrityError as error:
            _duplicate(f"workspace already exists: {workspace.workspace_id}", error)
        return _reconstruct(Workspace, _serialize(workspace))

    def get(self, workspace_id: str) -> Workspace | None:
        _require_id(workspace_id, "workspace_id")
        with self._database.read() as connection:
            row = connection.execute(
                "SELECT payload_json FROM workspaces WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
        return _reconstruct(Workspace, row["payload_json"]) if row else None

    def list(self) -> list[Workspace]:
        with self._database.read() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM workspaces ORDER BY workspace_id"
            ).fetchall()
        return [_reconstruct(Workspace, row["payload_json"]) for row in rows]

    def update(self, workspace: Workspace) -> Workspace:
        with self._database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE workspaces
                SET status = ?, created_at = ?, updated_at = ?, payload_json = ?
                WHERE workspace_id = ?
                """,
                (
                    workspace.status.value,
                    _timestamp(workspace.created_at),
                    _timestamp(workspace.updated_at),
                    _serialize(workspace),
                    workspace.workspace_id,
                ),
            )
            if cursor.rowcount == 0:
                raise RecordNotFoundError(
                    f"workspace not found: {workspace.workspace_id}"
                )
        return _reconstruct(Workspace, _serialize(workspace))


class SQLiteTaskRepository(TaskRepository):
    def __init__(
        self, database: SQLiteDatabase, workspaces: WorkspaceRepository
    ) -> None:
        self._database = database
        self._workspaces = workspaces

    def add(self, task: AgentTask) -> AgentTask:
        if self._workspaces.get(task.workspace_id) is None:
            raise RecordNotFoundError(f"workspace not found: {task.workspace_id}")
        if task.parent_task_id is not None:
            parent = self.get(task.workspace_id, task.parent_task_id)
            if parent is None:
                raise RecordNotFoundError(
                    "parent task not found in the child's workspace: "
                    f"{task.parent_task_id}"
                )
            validate_task_relationship(parent, task)
        try:
            with self._database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO tasks(
                        workspace_id, task_id, parent_task_id, status,
                        created_at, updated_at, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task.workspace_id,
                        task.task_id,
                        task.parent_task_id,
                        task.status.value,
                        _timestamp(task.created_at),
                        _timestamp(task.updated_at),
                        _serialize(task),
                    ),
                )
        except sqlite3.IntegrityError as error:
            _duplicate(
                f"task already exists in workspace {task.workspace_id}: {task.task_id}",
                error,
            )
        return _reconstruct(AgentTask, _serialize(task))

    def get(self, workspace_id: str, task_id: str) -> AgentTask | None:
        _require_id(workspace_id, "workspace_id")
        _require_id(task_id, "task ID")
        with self._database.read() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM tasks
                WHERE workspace_id = ? AND task_id = ?
                """,
                (workspace_id, task_id),
            ).fetchone()
        return _reconstruct(AgentTask, row["payload_json"]) if row else None

    def list(
        self, workspace_id: str, *, status: TaskStatus | None = None
    ) -> list[AgentTask]:
        _require_id(workspace_id, "workspace_id")
        query = "SELECT payload_json FROM tasks WHERE workspace_id = ?"
        values: list[str] = [workspace_id]
        if status is not None:
            query += " AND status = ?"
            values.append(status.value)
        query += " ORDER BY created_at, task_id"
        with self._database.read() as connection:
            rows = connection.execute(query, values).fetchall()
        return [_reconstruct(AgentTask, row["payload_json"]) for row in rows]

    def update(self, task: AgentTask) -> AgentTask:
        with self._database.transaction() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM tasks
                WHERE workspace_id = ? AND task_id = ?
                """,
                (task.workspace_id, task.task_id),
            ).fetchone()
            if row is None:
                raise RecordNotFoundError(
                    f"task not found in workspace {task.workspace_id}: {task.task_id}"
                )
            current = _reconstruct(AgentTask, row["payload_json"])
            if task.updated_at < current.updated_at:
                raise ValueError("task.updated_at cannot move backwards")
            if task.status is not current.status:
                validate_transition(current.status, task.status)
            if task.parent_task_id != current.parent_task_id:
                raise ImmutableRecordError("task parent relationship cannot be changed")
            connection.execute(
                """
                UPDATE tasks
                SET status = ?, created_at = ?, updated_at = ?, payload_json = ?
                WHERE workspace_id = ? AND task_id = ?
                """,
                (
                    task.status.value,
                    _timestamp(task.created_at),
                    _timestamp(task.updated_at),
                    _serialize(task),
                    task.workspace_id,
                    task.task_id,
                ),
            )
        return _reconstruct(AgentTask, _serialize(task))


class SQLiteResultRepository(ResultRepository):
    def __init__(self, database: SQLiteDatabase, tasks: TaskRepository) -> None:
        self._database = database
        self._tasks = tasks

    def add(self, result: AgentResult) -> AgentResult:
        task = self._tasks.get(result.workspace_id, result.task_id)
        if task is None:
            raise RecordNotFoundError(
                "result task not found in the result's workspace: "
                f"{result.task_id}"
            )
        validate_result_for_task(task, result)
        try:
            with self._database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO results(
                        workspace_id, result_id, task_id, created_at, payload_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        result.workspace_id,
                        result.result_id,
                        result.task_id,
                        _timestamp(result.created_at),
                        _serialize(result),
                    ),
                )
        except sqlite3.IntegrityError as error:
            _duplicate(
                "result already exists in workspace "
                f"{result.workspace_id}: {result.result_id}",
                error,
            )
        return _reconstruct(AgentResult, _serialize(result))

    def get(self, workspace_id: str, result_id: str) -> AgentResult | None:
        return self._get(workspace_id, result_id)

    def _get(self, workspace_id: str, result_id: str) -> AgentResult | None:
        _require_id(workspace_id, "workspace_id")
        _require_id(result_id, "result ID")
        with self._database.read() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM results
                WHERE workspace_id = ? AND result_id = ?
                """,
                (workspace_id, result_id),
            ).fetchone()
        return _reconstruct(AgentResult, row["payload_json"]) if row else None

    def list_for_task(self, workspace_id: str, task_id: str) -> list[AgentResult]:
        _require_id(workspace_id, "workspace_id")
        _require_id(task_id, "task ID")
        with self._database.read() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM results
                WHERE workspace_id = ? AND task_id = ?
                ORDER BY created_at, result_id
                """,
                (workspace_id, task_id),
            ).fetchall()
        return [_reconstruct(AgentResult, row["payload_json"]) for row in rows]


class SQLiteApprovalRepository(ApprovalRepository):
    def __init__(self, database: SQLiteDatabase, tasks: TaskRepository) -> None:
        self._database = database
        self._tasks = tasks

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
        try:
            with self._database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO approvals(
                        workspace_id, approval_id, task_id, proposal_id, status,
                        requested_at, resolved_at, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        approval.workspace_id,
                        approval.approval_id,
                        approval.task_id,
                        approval.proposal_id,
                        approval.status.value,
                        _timestamp(approval.requested_at),
                        _timestamp(approval.resolved_at)
                        if approval.resolved_at is not None
                        else None,
                        _serialize(approval),
                    ),
                )
        except sqlite3.IntegrityError as error:
            _duplicate(
                "approval already exists in workspace "
                f"{approval.workspace_id}: {approval.approval_id}",
                error,
            )
        return _reconstruct(ApprovalRequest, _serialize(approval))

    def get(self, workspace_id: str, approval_id: str) -> ApprovalRequest | None:
        _require_id(workspace_id, "workspace_id")
        _require_id(approval_id, "approval ID")
        with self._database.read() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM approvals
                WHERE workspace_id = ? AND approval_id = ?
                """,
                (workspace_id, approval_id),
            ).fetchone()
        return _reconstruct(ApprovalRequest, row["payload_json"]) if row else None

    def list(
        self, workspace_id: str, *, status: ApprovalStatus | None = None
    ) -> list[ApprovalRequest]:
        _require_id(workspace_id, "workspace_id")
        query = "SELECT payload_json FROM approvals WHERE workspace_id = ?"
        values: list[str] = [workspace_id]
        if status is not None:
            query += " AND status = ?"
            values.append(status.value)
        query += " ORDER BY requested_at, approval_id"
        with self._database.read() as connection:
            rows = connection.execute(query, values).fetchall()
        return [_reconstruct(ApprovalRequest, row["payload_json"]) for row in rows]

    def update(self, approval: ApprovalRequest) -> ApprovalRequest:
        with self._database.transaction() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM approvals
                WHERE workspace_id = ? AND approval_id = ?
                """,
                (approval.workspace_id, approval.approval_id),
            ).fetchone()
            if row is None:
                raise RecordNotFoundError(
                    f"approval not found in workspace {approval.workspace_id}: "
                    f"{approval.approval_id}"
                )
            current = _reconstruct(ApprovalRequest, row["payload_json"])
            identity_fields = (
                "workspace_id",
                "task_id",
                "proposal_id",
                "payload_hash",
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
            connection.execute(
                """
                UPDATE approvals
                SET status = ?, requested_at = ?, resolved_at = ?, payload_json = ?
                WHERE workspace_id = ? AND approval_id = ?
                """,
                (
                    approval.status.value,
                    _timestamp(approval.requested_at),
                    _timestamp(approval.resolved_at)
                    if approval.resolved_at is not None
                    else None,
                    _serialize(approval),
                    approval.workspace_id,
                    approval.approval_id,
                ),
            )
        return _reconstruct(ApprovalRequest, _serialize(approval))


class SQLiteDecisionRepository(DecisionRepository):
    def __init__(
        self,
        database: SQLiteDatabase,
        workspaces: WorkspaceRepository,
        tasks: TaskRepository,
        approvals: ApprovalRepository,
    ) -> None:
        self._database = database
        self._workspaces = workspaces
        self._tasks = tasks
        self._approvals = approvals

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
        try:
            with self._database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO decisions(
                        workspace_id, decision_id, related_task_id,
                        related_approval_id, created_at, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        decision.workspace_id,
                        decision.decision_id,
                        decision.related_task_id,
                        decision.related_approval_id,
                        _timestamp(decision.created_at),
                        _serialize(decision),
                    ),
                )
        except sqlite3.IntegrityError as error:
            _duplicate(
                "decision already exists in workspace "
                f"{decision.workspace_id}: {decision.decision_id}",
                error,
            )
        return _reconstruct(DecisionRecord, _serialize(decision))

    def get(self, workspace_id: str, decision_id: str) -> DecisionRecord | None:
        _require_id(workspace_id, "workspace_id")
        _require_id(decision_id, "decision ID")
        with self._database.read() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM decisions
                WHERE workspace_id = ? AND decision_id = ?
                """,
                (workspace_id, decision_id),
            ).fetchone()
        return _reconstruct(DecisionRecord, row["payload_json"]) if row else None

    def list(self, workspace_id: str) -> list[DecisionRecord]:
        _require_id(workspace_id, "workspace_id")
        with self._database.read() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM decisions
                WHERE workspace_id = ?
                ORDER BY created_at, decision_id
                """,
                (workspace_id,),
            ).fetchall()
        return [_reconstruct(DecisionRecord, row["payload_json"]) for row in rows]


class SQLiteEventRepository(EventRepository):
    def __init__(self, database: SQLiteDatabase, tasks: TaskRepository) -> None:
        self._database = database
        self._tasks = tasks

    def add(self, event: TaskEvent) -> TaskEvent:
        task = self._tasks.get(event.workspace_id, event.task_id)
        if task is None:
            raise RecordNotFoundError(
                "event task not found in the event's workspace: " f"{event.task_id}"
            )
        validate_event_for_task(task, event)
        try:
            with self._database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO task_events(
                        workspace_id, event_id, task_id, occurred_at, payload_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        event.workspace_id,
                        event.event_id,
                        event.task_id,
                        _timestamp(event.occurred_at),
                        _serialize(event),
                    ),
                )
        except sqlite3.IntegrityError as error:
            _duplicate(
                "event already exists in workspace "
                f"{event.workspace_id}: {event.event_id}",
                error,
            )
        return _reconstruct(TaskEvent, _serialize(event))

    def get(self, workspace_id: str, event_id: str) -> TaskEvent | None:
        _require_id(workspace_id, "workspace_id")
        _require_id(event_id, "event ID")
        with self._database.read() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM task_events
                WHERE workspace_id = ? AND event_id = ?
                """,
                (workspace_id, event_id),
            ).fetchone()
        return _reconstruct(TaskEvent, row["payload_json"]) if row else None

    def list_for_task(self, workspace_id: str, task_id: str) -> list[TaskEvent]:
        _require_id(workspace_id, "workspace_id")
        _require_id(task_id, "task ID")
        with self._database.read() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM task_events
                WHERE workspace_id = ? AND task_id = ?
                ORDER BY occurred_at, event_id
                """,
                (workspace_id, task_id),
            ).fetchall()
        return [_reconstruct(TaskEvent, row["payload_json"]) for row in rows]


class SQLiteRepositories:
    """Convenience container with one schema and all SQLite repositories."""

    def __init__(self, path: str | Path = DEFAULT_DATABASE_PATH) -> None:
        self.database = SQLiteDatabase(path)
        self.workspaces = SQLiteWorkspaceRepository(self.database)
        self.tasks = SQLiteTaskRepository(self.database, self.workspaces)
        self.results = SQLiteResultRepository(self.database, self.tasks)
        self.approvals = SQLiteApprovalRepository(self.database, self.tasks)
        self.decisions = SQLiteDecisionRepository(
            self.database, self.workspaces, self.tasks, self.approvals
        )
        self.events = SQLiteEventRepository(self.database, self.tasks)

    @contextmanager
    def transaction(self) -> Iterator[SQLiteRepositories]:
        """Coordinate several repository operations in one SQLite transaction."""

        with self.database.transaction():
            yield self
