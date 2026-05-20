from __future__ import annotations

import requests

from .provider_base import LLMProvider, LLMResponse


class OpenAICompatProvider(LLMProvider):
    def __init__(self, base_url: str, api_key: str | None, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def complete(self, prompt: str) -> LLMResponse:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": prompt},
            ],
        }
        resp = requests.post(f"{self.base_url}/v1/chat/completions", headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return LLMResponse(content=content)
