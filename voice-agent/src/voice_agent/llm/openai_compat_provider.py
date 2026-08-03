from __future__ import annotations

import ipaddress
import json
from urllib.parse import urlsplit, urlunsplit

import requests

from .provider_base import LLMProvider, LLMResponse


AUTO_ROUTER_MODEL_PREFIX = "auto/"
_LOCAL_HOST_SUFFIXES = (
    ".lan",
    ".local",
    ".internal",
    ".ts.net",
)
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


class LLMProviderError(Exception):
    """Raised when an OpenAI-compatible endpoint returns an error payload
    (including lmstudio's habit of replying with a plain JSON ``{"error": ...}``
    body and HTTP 200, which ``raise_for_status`` does not catch)."""


def normalize_openai_base_url(base_url: str) -> str:
    """Return an endpoint root that can safely receive ``/v1/...`` suffixes.

    Operators commonly provide either ``http://router:8088`` or
    ``http://router:8088/v1`` as an OpenAI base URL. The provider owns the
    endpoint suffix, so normalize an optional trailing ``/v1`` to prevent
    accidental requests to ``/v1/v1/chat/completions``.
    """

    raw = (base_url or "").strip()
    if not raw:
        raise ValueError("OpenAI-compatible base URL is required")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Invalid OpenAI-compatible base URL: {base_url!r}")
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3].rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def _is_private_router_host(hostname: str) -> bool:
    host = hostname.strip().lower().rstrip(".")
    if host in {"localhost", "host.docker.internal"}:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Single-label names are local Docker/LAN service names. Qualified
        # names must use an explicitly local or Tailscale suffix.
        return "." not in host or host.endswith(_LOCAL_HOST_SUFFIXES)
    return (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address in _CGNAT_NETWORK
    )


def validate_auto_router_base_url(base_url: str) -> str:
    """Normalize and reject public endpoints for ``auto/*`` model aliases.

    auto-router's aliases are strict-offline contracts. A Sophia deployment
    using one of those aliases must not silently point at OpenAI, OpenRouter, or
    another public gateway.
    """

    normalized = normalize_openai_base_url(base_url)
    host = urlsplit(normalized).hostname or ""
    if not _is_private_router_host(host):
        raise ValueError(
            "auto-router model aliases require a local, LAN, Docker, or "
            f"Tailscale base URL; received host {host!r}"
        )
    return normalized


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
        resolved_task_model = task_model or model
        uses_auto_router = any(
            candidate.startswith(AUTO_ROUTER_MODEL_PREFIX)
            for candidate in (model, resolved_task_model)
        )
        self.base_url = (
            validate_auto_router_base_url(base_url)
            if uses_auto_router
            else normalize_openai_base_url(base_url)
        )
        self.api_key = api_key
        self.model = model
        self.task_model = resolved_task_model
        self.timeout = timeout
        self.task_timeout = task_timeout or timeout
        self.uses_auto_router = uses_auto_router

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
        resp = requests.post(
            f"{self.base_url}/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=timeout if timeout is not None else self.task_timeout,
        )
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
