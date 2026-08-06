"""Opt-in LiteLLM adapter preserving KAIOS's external-model behavior."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .base import (
    ModelOutput,
    ModelProvider,
    ModelProviderConfigurationError,
    ModelProviderResponseError,
    ModelRequest,
)


CompletionFunction = Callable[..., Any]


class LiteLLMModelProvider(ModelProvider):
    def __init__(
        self,
        *,
        model: str | None,
        api_key: str | None,
        api_base: str | None = None,
        completion_fn: CompletionFunction | None = None,
    ) -> None:
        if not model or not model.strip():
            raise ModelProviderConfigurationError(
                "LiteLLM requires KAIOS_LLM_MODEL or an explicit model"
            )
        if not api_key or not api_key.strip():
            raise ModelProviderConfigurationError(
                "LiteLLM requires KAIOS_LLM_API_KEY or an explicit api_key"
            )
        self._model = model
        self._api_key = api_key
        self._api_base = api_base
        self._completion_fn = completion_fn

    @property
    def provider_id(self) -> str:
        return "litellm"

    @property
    def requires_network(self) -> bool:
        return True

    @property
    def requires_paid_access(self) -> bool:
        return True

    def generate(self, request: ModelRequest) -> ModelOutput:
        completion = self._completion_fn or _load_litellm_completion()
        response = completion(
            model=request.model or self._model,
            messages=[
                {"role": "system", "content": request.system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(request.input_data, ensure_ascii=False),
                },
            ],
            api_key=self._api_key,
            api_base=self._api_base,
            temperature=0.2,
            max_tokens=2000,
        )
        try:
            content = response.choices[0].message.content
            cleaned = _clean_json(content)
            data = json.loads(cleaned)
        except (AttributeError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise ModelProviderResponseError(
                "LiteLLM returned an invalid structured response"
            ) from error
        return ModelOutput(data=data, provider_id=self.provider_id)


def _load_litellm_completion() -> CompletionFunction:
    from litellm import completion

    return completion


def _clean_json(text: str) -> str:
    if not isinstance(text, str):
        raise ModelProviderResponseError("LiteLLM response content must be text")
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0]
    return text.strip()
