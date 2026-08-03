from __future__ import annotations

import ipaddress
import json
import time
import uuid
from typing import Any
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
_ROUTE_HEADER_MAP = {
    "x-auto-router-provider": "provider",
    "x-auto-router-model": "model",
    "x-auto-router-stage": "stage",
    "x-auto-router-profile": "profile",
}


class LLMProviderError(Exception):
    """Structured failure from an OpenAI-compatible inference endpoint."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.metadata = dict(metadata or {})


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


def _coerce_retry_after(value: Any) -> int | None:
    try:
        return max(int(float(str(value))), 0)
    except (TypeError, ValueError):
        return None


def _response_headers(resp: Any) -> dict[str, str]:
    raw_headers = getattr(resp, "headers", {}) or {}
    try:
        return {str(key).lower(): str(value) for key, value in raw_headers.items()}
    except AttributeError:
        return {}


def _error_detail(payload: Any) -> str:
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("error")
        if isinstance(detail, dict):
            return str(
                detail.get("message")
                or detail.get("error")
                or detail.get("detail")
                or detail
            )
        if detail is not None:
            return str(detail)
    return "upstream inference request failed"


def _raise_for_http_error(resp: Any) -> None:
    status_code = int(getattr(resp, "status_code", 200) or 200)
    if status_code < 400:
        resp.raise_for_status()
        return
    try:
        payload = resp.json()
    except Exception:
        payload = None
    headers = _response_headers(resp)
    retry_after = _coerce_retry_after(headers.get("retry-after"))
    detail = _error_detail(payload)
    if status_code == 503:
        message = (
            "auto-router has no admitted local capacity; "
            f"request was not sent to a hosted provider: {detail}"
        )
    elif status_code == 429:
        message = f"auto-router admission is saturated: {detail}"
    else:
        message = f"LLM endpoint returned HTTP {status_code}: {detail}"
    raise LLMProviderError(
        message,
        status_code=status_code,
        retry_after_seconds=retry_after,
        metadata={"retry_after_seconds": retry_after},
    )


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
        self.last_route_metadata: dict[str, Any] = {}

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request_metadata(
        self,
        workload_class: str,
        *,
        latency_target_ms: int,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "correlation_id": str(uuid.uuid4()),
            "source_repo": "Sophia",
            "source_service": "voice-agent",
            "workload_class": workload_class,
            "privacy": "personal",
            "local_only": True,
            "allow_cloud": False,
            "priority": "interactive",
            "latency_target_ms": latency_target_ms,
            "queued_at_ms": int(time.time() * 1000),
        }
        if overrides:
            metadata.update(
                {
                    str(key): value
                    for key, value in overrides.items()
                    if value is not None
                }
            )
        # An auto/* alias is a strict-offline contract. Caller overrides may
        # enrich the request but cannot weaken local/privacy admission.
        metadata["privacy"] = "personal"
        metadata["local_only"] = True
        metadata["allow_cloud"] = False
        return metadata

    def _route_metadata(
        self,
        resp: Any,
        *,
        requested_model: str,
        request_metadata: dict[str, Any],
        payload: Any | None = None,
    ) -> dict[str, Any]:
        route: dict[str, Any] = {
            "correlation_id": request_metadata.get("correlation_id"),
            "session_id": request_metadata.get("session_id"),
            "workload_class": request_metadata.get("workload_class"),
            "requested_model": requested_model,
            "status_code": int(getattr(resp, "status_code", 200) or 200),
        }
        if isinstance(payload, dict) and isinstance(payload.get("auto_router"), dict):
            route.update(payload["auto_router"])
        headers = _response_headers(resp)
        for header, key in _ROUTE_HEADER_MAP.items():
            if headers.get(header):
                route[key] = headers[header]
        retry_after = _coerce_retry_after(headers.get("retry-after"))
        if retry_after is not None:
            route["retry_after_seconds"] = retry_after
        return {key: value for key, value in route.items() if value is not None}

    def complete(
        self,
        prompt: str,
        timeout: float | None = None,
        request_metadata: dict[str, Any] | None = None,
    ) -> LLMResponse:
        metadata = self._request_metadata(
            "task_extraction",
            latency_target_ms=10_000,
            overrides=request_metadata,
        )
        payload: dict[str, Any] = {
            "model": self.task_model,
            "messages": [
                {"role": "user", "content": prompt},
            ],
        }
        if self.uses_auto_router:
            payload["metadata"] = metadata
        started = time.perf_counter()
        resp = requests.post(
            f"{self.base_url}/v1/chat/completions",
            headers=self._headers(),
            json=payload,
            timeout=timeout if timeout is not None else self.task_timeout,
        )
        try:
            _raise_for_http_error(resp)
        except LLMProviderError as exc:
            self.last_route_metadata = {
                **metadata,
                "requested_model": self.task_model,
                "status_code": exc.status_code,
                "retry_after_seconds": exc.retry_after_seconds,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "error": str(exc),
            }
            raise
        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMProviderError("LLM returned a non-JSON response body") from exc
        _raise_if_error(data)
        content = data["choices"][0]["message"]["content"]
        route = self._route_metadata(
            resp,
            requested_model=self.task_model,
            request_metadata=metadata,
            payload=data,
        )
        route["latency_ms"] = int((time.perf_counter() - started) * 1000)
        self.last_route_metadata = route
        return LLMResponse(content=content, metadata=route)

    def stream_complete(
        self,
        messages,
        *,
        temperature: float = 0.7,
        max_tokens: int = 800,
        request_metadata: dict[str, Any] | None = None,
    ):
        metadata = self._request_metadata(
            "interactive_voice",
            latency_target_ms=1_500,
            overrides=request_metadata,
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.uses_auto_router:
            payload["metadata"] = metadata
        started = time.perf_counter()
        resp = requests.post(
            f"{self.base_url}/v1/chat/completions",
            headers=self._headers(),
            json=payload,
            stream=True,
            timeout=self.timeout,
        )
        try:
            _raise_for_http_error(resp)
        except LLMProviderError as exc:
            self.last_route_metadata = {
                **metadata,
                "requested_model": self.model,
                "status_code": exc.status_code,
                "retry_after_seconds": exc.retry_after_seconds,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "error": str(exc),
            }
            raise
        route = self._route_metadata(
            resp,
            requested_model=self.model,
            request_metadata=metadata,
        )
        first_token_at: float | None = None
        try:
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
                            if first_token_at is None:
                                first_token_at = time.perf_counter()
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
        finally:
            ended = time.perf_counter()
            route["latency_ms"] = int((ended - started) * 1000)
            if first_token_at is not None:
                route["ttft_ms"] = int((first_token_at - started) * 1000)
            self.last_route_metadata = route
