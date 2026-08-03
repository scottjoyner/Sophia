from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMResponse:
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class LLMProvider:
    def complete(self, prompt: str) -> LLMResponse:
        raise NotImplementedError


class MockProvider(LLMProvider):
    def complete(self, prompt: str) -> LLMResponse:
        return LLMResponse(content=f"Mock response to: {prompt}")

    def stream_complete(self, messages, *, temperature: float = 0.7, max_tokens: int = 800):
        if isinstance(messages, str):
            text = messages
        else:
            text = messages[-1]["content"] if messages else ""
        yield f"[mock] Echoing your message: {text}"
