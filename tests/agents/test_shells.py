import pytest

from kaios.agents import (
    FINANCE_AGENT_ID,
    MARKETING_AGENT_ID,
    STORE_OPERATIONS_AGENT_ID,
    CapabilityShellError,
    FinanceAgent,
    MarketingAgent,
    ProductIntelligenceAgent,
    StoreOperationsAgent,
    UnsupportedTaskTypeError,
    build_initial_agent_registry,
)
from kaios.core.contracts import AgentTask


def test_initial_registry_contains_product_intelligence_and_three_safe_shells():
    registry = build_initial_agent_registry(ProductIntelligenceAgent())

    capabilities = {item.agent_id: item for item in registry.capabilities()}

    assert set(capabilities) == {
        "product_intelligence",
        STORE_OPERATIONS_AGENT_ID,
        MARKETING_AGENT_ID,
        FINANCE_AGENT_ID,
    }
    for agent_id in (
        STORE_OPERATIONS_AGENT_ID,
        MARKETING_AGENT_ID,
        FINANCE_AGENT_ID,
    ):
        assert capabilities[agent_id].supported_task_types == frozenset()
        assert capabilities[agent_id].allowed_action_types == frozenset()


@pytest.mark.parametrize(
    "agent",
    [StoreOperationsAgent(), MarketingAgent(), FinanceAgent()],
)
def test_capability_shells_reject_direct_and_registry_execution(agent):
    registry = build_initial_agent_registry(ProductIntelligenceAgent())
    task = AgentTask(
        workspace_id="pod",
        task_type="attempt_operation",
        assigned_agent=agent.agent_id,
    )

    with pytest.raises(UnsupportedTaskTypeError):
        registry.resolve(task, workspace_id="pod")
    with pytest.raises(CapabilityShellError, match="no operational logic"):
        agent.handle(task)
