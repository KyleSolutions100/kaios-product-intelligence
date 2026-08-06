import pytest

from kaios.agents import (
    AgentNotRegisteredError,
    AgentRegistry,
    BaseAgent,
    DuplicateAgentError,
    UnsupportedTaskTypeError,
)
from kaios.core.contracts import AgentResult, AgentTask
from kaios.core.workspaces import WorkspaceBoundaryError


class DummyAgent(BaseAgent):
    @property
    def agent_id(self) -> str:
        return "dummy"

    @property
    def supported_task_types(self) -> frozenset[str]:
        return frozenset({"supported"})

    @property
    def allowed_action_types(self) -> frozenset[str]:
        return frozenset({"safe_action"})

    def handle(self, task: AgentTask) -> AgentResult:
        return AgentResult.for_task(
            task, agent_id=self.agent_id, summary="Handled"
        )


def make_task(
    *, workspace_id: str = "pod", task_type: str = "supported"
) -> AgentTask:
    return AgentTask(
        workspace_id=workspace_id,
        task_type=task_type,
        assigned_agent="dummy",
    )


def test_registry_registers_agents_and_exposes_immutable_capabilities():
    registry = AgentRegistry()
    registry.register(DummyAgent())

    capabilities = registry.capabilities()

    assert len(capabilities) == 1
    assert capabilities[0].agent_id == "dummy"
    assert capabilities[0].supported_task_types == frozenset({"supported"})
    assert capabilities[0].allowed_action_types == frozenset({"safe_action"})


def test_registry_rejects_duplicate_agent_ids():
    registry = AgentRegistry()
    registry.register(DummyAgent())

    with pytest.raises(DuplicateAgentError, match="already registered"):
        registry.register(DummyAgent())


def test_registry_rejects_unknown_agent_and_unsupported_task_type():
    registry = AgentRegistry()
    registry.register(DummyAgent())
    unknown = make_task().model_copy(update={"assigned_agent": "missing"})

    with pytest.raises(AgentNotRegisteredError, match="not registered"):
        registry.resolve(unknown, workspace_id="pod")
    with pytest.raises(UnsupportedTaskTypeError, match="does not support"):
        registry.resolve(make_task(task_type="unsupported"), workspace_id="pod")


def test_registry_rejects_cross_workspace_routing_before_agent_execution():
    registry = AgentRegistry()
    registry.register(DummyAgent())

    with pytest.raises(WorkspaceBoundaryError, match="routing workspace"):
        registry.route(make_task(workspace_id="pod"), workspace_id="trading")
