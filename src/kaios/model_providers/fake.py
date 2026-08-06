"""Predictable model provider for unit and integration tests."""

from __future__ import annotations

from copy import deepcopy
from typing import Iterable

from .base import JSONValue, ModelOutput, ModelProvider, ModelRequest


class FakeModelProvider(ModelProvider):
    def __init__(
        self,
        output: JSONValue = None,
        *,
        outputs: Iterable[JSONValue] | None = None,
        failure: Exception | None = None,
    ) -> None:
        if outputs is not None and output is not None:
            raise ValueError("provide output or outputs, not both")
        predefined = list(outputs) if outputs is not None else [
            [] if output is None else output
        ]
        if not predefined:
            raise ValueError("at least one fake output is required")
        self._outputs = deepcopy(predefined)
        self._failure = failure
        self._next_output = 0
        self.calls: list[ModelRequest] = []

    @property
    def provider_id(self) -> str:
        return "fake"

    @property
    def requires_network(self) -> bool:
        return False

    @property
    def requires_paid_access(self) -> bool:
        return False

    def generate(self, request: ModelRequest) -> ModelOutput:
        self.calls.append(deepcopy(request))
        if self._failure is not None:
            raise self._failure
        index = min(self._next_output, len(self._outputs) - 1)
        self._next_output += 1
        return ModelOutput(
            data=deepcopy(self._outputs[index]), provider_id=self.provider_id
        )
