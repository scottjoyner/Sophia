from __future__ import annotations

import json

import requests

from .provider_base import LLMProvider, LLMResponse


class OpenAICompatProvider(LLMProvider):
    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout: float = 60.0,
        task_model: str | None = None,
        task_timeout: float | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.task_model = task_model or model
        self.timeout = timeout
        self.task_timeout = task_timeout or timeout

    def complete(self, prompt: str) -> LLMResponse:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.task_model,
            "messages": [
                {"role": "user", "content": prompt},
            ],
        }
        resp = requests.post(f"{self.base_url}/v1/chat/completions", headers=headers, json=payload, timeout=self.task_timeout)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return LLMResponse(content=content)

    def stream_complete(self, messages, *, temperature: float = 0.7, max_tokens: int = 800):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        resp = requests.post(
            f"{self.base_url}/v1/chat/completions",
            headers=headers,
            json=payload,
            stream=True,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        for raw in resp.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
                delta = obj["choices"][0]["delta"].get("content")
                if not delta:
                    # Some local/reasoning models stream their output under
                    # reasoning_content instead of content; surface it so the
                    # caller still sees a response.
                    delta = obj["choices"][0]["delta"].get("reasoning_content")
                if delta:
                    yield delta
            except Exception:
                continue
