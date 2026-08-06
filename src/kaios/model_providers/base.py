"""Persistence-free contracts for structured model execution."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


JSONScalar = str | int | float | bool | None
JSONValue = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]


class ModelProviderError(RuntimeError):
    """Base error for provider configuration and execution failures."""


class ModelProviderConfigurationError(ModelProviderError):
    """Raised when a selected provider lacks required configuration."""


class ModelProviderResponseError(ModelProviderError):
    """Raised when a provider does not return valid structured output."""


@dataclass(frozen=True)
class ModelRequest:
    """Structured input shared by all KAIOS model providers."""

    task: str
    input_data: dict[str, Any]
    system_prompt: str = ""
    model: str | None = None

    def __post_init__(self) -> None:
        if not self.task.strip():
            raise ValueError("model request task is required")
        if self.model is not None and not self.model.strip():
            raise ValueError("model request model cannot be blank")


@dataclass(frozen=True)
class ModelOutput:
    """Structured output returned by a named provider."""

    data: JSONValue
    provider_id: str

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("model output provider_id is required")


class ModelProvider(ABC):
    """Provider-independent interface for deterministic or external models."""

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Return the stable provider identity used in logs and results."""

    @property
    @abstractmethod
    def requires_network(self) -> bool:
        """Whether generate() may require external network access."""

    @property
    @abstractmethod
    def requires_paid_access(self) -> bool:
        """Whether generate() may consume a paid model service."""

    @abstractmethod
    def generate(self, request: ModelRequest) -> ModelOutput:
        """Return JSON-compatible structured output for a structured request."""
