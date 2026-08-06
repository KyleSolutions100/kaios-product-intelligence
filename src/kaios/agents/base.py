"""Common contract for specialist agents in the KAIOS runtime."""

from __future__ import annotations

from abc import ABC, abstractmethod

from kaios.core.contracts import AgentResult, AgentTask


class BaseAgent(ABC):
    @property
    @abstractmethod
    def agent_id(self) -> str:
        """Return the stable ID used to assign tasks to this agent."""

    @property
    @abstractmethod
    def supported_task_types(self) -> frozenset[str]:
        """Return the task types this agent is permitted to handle."""

    @property
    @abstractmethod
    def allowed_action_types(self) -> frozenset[str]:
        """Return action types this agent may propose or execute."""

    @abstractmethod
    def handle(self, task: AgentTask) -> AgentResult:
        """Handle one validated task and return a workspace-scoped result."""
