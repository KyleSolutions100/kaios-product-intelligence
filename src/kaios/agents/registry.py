"""Capability-aware, workspace-safe registration and routing of agents."""

from __future__ import annotations

from dataclasses import dataclass

from kaios.core.contracts import AgentResult, AgentTask
from kaios.core.workspaces import WorkspaceBoundaryError

from .base import BaseAgent


class AgentRegistryError(RuntimeError):
    """Base error for registration and task-routing failures."""


class DuplicateAgentError(AgentRegistryError):
    """Raised when an agent ID is registered more than once."""


class AgentNotRegisteredError(AgentRegistryError):
    """Raised when a task names an unknown agent."""


class UnsupportedTaskTypeError(AgentRegistryError):
    """Raised when an assigned agent does not support the task type."""


@dataclass(frozen=True)
class AgentCapabilities:
    agent_id: str
    supported_task_types: frozenset[str]
    allowed_action_types: frozenset[str]


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        if agent.agent_id in self._agents:
            raise DuplicateAgentError(f"agent already registered: {agent.agent_id}")
        self._agents[agent.agent_id] = agent

    def capabilities(self) -> list[AgentCapabilities]:
        return [
            AgentCapabilities(
                agent_id=agent.agent_id,
                supported_task_types=frozenset(agent.supported_task_types),
                allowed_action_types=frozenset(agent.allowed_action_types),
            )
            for agent in sorted(self._agents.values(), key=lambda item: item.agent_id)
        ]

    def resolve(self, task: AgentTask, *, workspace_id: str) -> BaseAgent:
        if not workspace_id:
            raise ValueError("workspace_id is required for agent routing")
        if task.workspace_id != workspace_id:
            raise WorkspaceBoundaryError(
                "task workspace does not match the routing workspace: "
                f"{task.workspace_id} != {workspace_id}"
            )
        agent = self._agents.get(task.assigned_agent)
        if agent is None:
            raise AgentNotRegisteredError(
                f"assigned agent is not registered: {task.assigned_agent}"
            )
        if task.task_type not in agent.supported_task_types:
            raise UnsupportedTaskTypeError(
                f"agent {agent.agent_id} does not support task type {task.task_type}"
            )
        return agent

    def route(self, task: AgentTask, *, workspace_id: str) -> AgentResult:
        agent = self.resolve(task, workspace_id=workspace_id)
        return agent.handle(task)
