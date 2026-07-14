from __future__ import annotations

from ..config import AppConfig
from .openai_compat_provider import OpenAICompatProvider
from .provider_base import LLMProvider, MockProvider


class RalphLoop:
    def __init__(self, config: AppConfig):
        self.config = config
        self.provider = self._build_provider()

    def _build_provider(self) -> LLMProvider:
        if self.config.llm.provider in {"openai", "hermes"} and self.config.llm.base_url:
            return OpenAICompatProvider(self.config.llm.base_url, self.config.llm.api_key, self.config.llm.model)
        return MockProvider()

    def run(self, transcript: str) -> str:
        prompt = f"{self.config.llm.system_prompt}\n\n{transcript}"
        return self.provider.complete(prompt).content
