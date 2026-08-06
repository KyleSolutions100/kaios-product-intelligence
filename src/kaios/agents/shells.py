"""Registered capability shells for specialist agents not yet implemented."""

from __future__ import annotations

from kaios.core.contracts import AgentResult, AgentTask

from .base import BaseAgent
from .product_intelligence import ProductIntelligenceAgent
from .registry import AgentRegistry


STORE_OPERATIONS_AGENT_ID = "store_operations"
MARKETING_AGENT_ID = "marketing"
FINANCE_AGENT_ID = "finance"


class CapabilityShellError(RuntimeError):
    """Raised if an unimplemented specialist shell is invoked directly."""


class _CapabilityShellAgent(BaseAgent):
    _agent_id = ""

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def supported_task_types(self) -> frozenset[str]:
        return frozenset()

    @property
    def allowed_action_types(self) -> frozenset[str]:
        return frozenset()

    def handle(self, task: AgentTask) -> AgentResult:
        raise CapabilityShellError(
            f"agent {self.agent_id} is a capability shell with no operational logic"
        )


class StoreOperationsAgent(_CapabilityShellAgent):
    _agent_id = STORE_OPERATIONS_AGENT_ID


class MarketingAgent(_CapabilityShellAgent):
    _agent_id = MARKETING_AGENT_ID


class FinanceAgent(_CapabilityShellAgent):
    _agent_id = FINANCE_AGENT_ID


def build_initial_agent_registry(
    product_intelligence_agent: BaseAgent | None = None,
) -> AgentRegistry:
    """Register Product Intelligence and safe shells for future specialists."""

    registry = AgentRegistry()
    registry.register(product_intelligence_agent or ProductIntelligenceAgent())
    registry.register(StoreOperationsAgent())
    registry.register(MarketingAgent())
    registry.register(FinanceAgent())
    return registry
