"""Persistence-neutral repository interfaces and temporary implementations."""

from .interfaces import (
    ApprovalRepository,
    DecisionRepository,
    DuplicateRecordError,
    EventRepository,
    ImmutableRecordError,
    RecordNotFoundError,
    RepositoryError,
    ResultRepository,
    TaskRepository,
    WorkspaceRepository,
)
from .memory import (
    InMemoryRepositories,
    MemoryApprovalRepository,
    MemoryDecisionRepository,
    MemoryEventRepository,
    MemoryResultRepository,
    MemoryTaskRepository,
    MemoryWorkspaceRepository,
)

__all__ = [
    "ApprovalRepository",
    "DecisionRepository",
    "DuplicateRecordError",
    "EventRepository",
    "ImmutableRecordError",
    "InMemoryRepositories",
    "MemoryApprovalRepository",
    "MemoryDecisionRepository",
    "MemoryEventRepository",
    "MemoryResultRepository",
    "MemoryTaskRepository",
    "MemoryWorkspaceRepository",
    "RecordNotFoundError",
    "RepositoryError",
    "ResultRepository",
    "TaskRepository",
    "WorkspaceRepository",
]
