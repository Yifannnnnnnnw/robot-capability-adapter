"""Tiny injected JSON-only LLM boundary used by the demo stages."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from ..foundation.errors import ContractError


class JsonGenerator(Protocol):
    def generate_json(self, stage: str, prompt: str, inputs: Mapping[str, Any]) -> dict[str, Any]:
        """Return one JSON object for an isolated, named stage."""


class FixtureJsonGenerator:
    """Deterministic test adapter; it has no provider, network, or hidden state."""

    def __init__(self, responses: Sequence[dict[str, Any]] | Callable[[str, str, Mapping[str, Any]], dict[str, Any]]):
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    def generate_json(self, stage: str, prompt: str, inputs: Mapping[str, Any]) -> dict[str, Any]:
        self.calls.append({"stage": stage, "prompt": prompt, "inputs": dict(inputs)})
        if callable(self._responses):
            result = self._responses(stage, prompt, inputs)
        else:
            index = len(self.calls) - 1
            if index >= len(self._responses):
                raise ContractError("fixture generator has no response for this call")
            result = self._responses[index]
        if not isinstance(result, dict):
            raise ContractError("JSON generator must return an object")
        return result
