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

__all__ = [
    "AgentCapabilities",
    "AgentNotRegisteredError",
    "AgentRegistry",
    "AgentRegistryError",
    "AgentRepositoryUnitOfWork",
    "AgentTaskService",
    "AgentTaskValidationError",
    "BaseAgent",
    "DuplicateAgentError",
    "PRODUCT_INTELLIGENCE_AGENT_ID",
    "PRODUCT_RESEARCH_TASK_TYPE",
    "ProductIntelligenceAgent",
    "ProductResearchInput",
    "ResearchFailure",
    "TaskExecutionError",
    "UnsupportedTaskTypeError",
]
