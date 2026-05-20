from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LLMResponse:
    content: str


class LLMProvider:
    def complete(self, prompt: str) -> LLMResponse:
        raise NotImplementedError


class MockProvider(LLMProvider):
    def complete(self, prompt: str) -> LLMResponse:
        return LLMResponse(content=f"Mock response to: {prompt}")
