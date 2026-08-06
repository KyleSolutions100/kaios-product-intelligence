"""Specialist agent contracts, registration, and task execution."""

from .base import BaseAgent
from .product_intelligence import (
    PRODUCT_INTELLIGENCE_AGENT_ID,
    PRODUCT_RESEARCH_TASK_TYPE,
    AgentTaskValidationError,
    ProductIntelligenceAgent,
    ProductResearchInput,
    ResearchFailure,
)
from .registry import (
    AgentCapabilities,
    AgentNotRegisteredError,
    AgentRegistry,
    AgentRegistryError,
    DuplicateAgentError,
    UnsupportedTaskTypeError,
)
from .service import AgentRepositoryUnitOfWork, AgentTaskService, TaskExecutionError
from .shells import (
    FINANCE_AGENT_ID,
    MARKETING_AGENT_ID,
    STORE_OPERATIONS_AGENT_ID,
    CapabilityShellError,
    FinanceAgent,
    MarketingAgent,
    StoreOperationsAgent,
    build_initial_agent_registry,
)

__all__ = [
    "AgentCapabilities",
    "AgentNotRegisteredError",
    "AgentRegistry",
    "AgentRegistryError",
    "AgentRepositoryUnitOfWork",
    "AgentTaskService",
    "AgentTaskValidationError",
    "BaseAgent",
    "CapabilityShellError",
    "DuplicateAgentError",
    "FINANCE_AGENT_ID",
    "FinanceAgent",
    "MARKETING_AGENT_ID",
    "MarketingAgent",
    "PRODUCT_INTELLIGENCE_AGENT_ID",
    "PRODUCT_RESEARCH_TASK_TYPE",
    "ProductIntelligenceAgent",
    "ProductResearchInput",
    "ResearchFailure",
    "STORE_OPERATIONS_AGENT_ID",
    "StoreOperationsAgent",
    "TaskExecutionError",
    "UnsupportedTaskTypeError",
    "build_initial_agent_registry",
]
