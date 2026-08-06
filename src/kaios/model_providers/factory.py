"""Configuration-driven construction of KAIOS model providers."""

from __future__ import annotations

import os

from .base import ModelProvider, ModelProviderConfigurationError
from .fake import FakeModelProvider
from .litellm import LiteLLMModelProvider
from .rules import RulesModelProvider


PROVIDER_ALIASES = {"openai": "litellm"}
SUPPORTED_PROVIDERS = frozenset({"rules", "fake", "litellm"})


def normalize_provider_name(name: str) -> str:
    normalized = name.strip().lower()
    normalized = PROVIDER_ALIASES.get(normalized, normalized)
    if normalized not in SUPPORTED_PROVIDERS:
        choices = ", ".join(sorted(SUPPORTED_PROVIDERS))
        raise ModelProviderConfigurationError(
            f"unknown model provider '{name}'; choose one of: {choices}"
        )
    return normalized


def create_model_provider(
    name: str,
    *,
    model: str | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
) -> ModelProvider:
    provider_name = normalize_provider_name(name)
    if provider_name == "rules":
        return RulesModelProvider()
    if provider_name == "fake":
        return FakeModelProvider()
    return LiteLLMModelProvider(
        model=model or os.getenv("KAIOS_LLM_MODEL"),
        api_key=api_key or os.getenv("KAIOS_LLM_API_KEY"),
        api_base=api_base or os.getenv("KAIOS_LLM_API_BASE"),
    )
