from __future__ import annotations

import json

import requests

from .provider_base import LLMProvider, LLMResponse


class LLMProviderError(Exception):
    """Raised when an OpenAI-compatible endpoint returns an error payload
    (including lmstudio's habit of replying with a plain JSON ``{"error": ...}``
    body and HTTP 200, which ``raise_for_status`` does not catch)."""


def _raise_if_error(obj: object) -> None:
    if not isinstance(obj, dict):
        return
    err = obj.get("error")
    if err is None:
        return
    if isinstance(err, dict):
        msg = err.get("message") or err.get("type") or "unknown error"
    else:
        msg = str(err)
    raise LLMProviderError("LLM endpoint error: " + str(msg))


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

    def complete(self, prompt: str, timeout: float | None = None) -> LLMResponse:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.task_model,
            "messages": [
                {"role": "user", "content": prompt},
            ],
        }
        resp = requests.post(f"{self.base_url}/v1/chat/completions", headers=headers, json=payload, timeout=timeout if timeout is not None else self.task_timeout)
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            raise LLMProviderError("LLM returned a non-JSON response body")
        _raise_if_error(data)
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
            if line.startswith("data:"):
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except Exception:
                    continue
                _raise_if_error(obj)
                try:
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
            else:
                # Non-SSE body: lmstudio frequently returns a plain JSON error
                # object (not wrapped in `data:`) with HTTP 200. Surface it so
                # the caller can retry against another endpoint.
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                _raise_if_error(obj)
