from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterable

from fastapi import FastAPI, Request, Response

DEFAULT_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(self), geolocation=(self)",
    "Cross-Origin-Opener-Policy": "same-origin",
}


def make_request_id(existing: str | None = None) -> str:
    value = (existing or "").strip()
    if value and len(value) <= 128:
        return value
    return uuid.uuid4().hex


def apply_security_headers(response: Response, *, headers: dict[str, str] | None = None) -> None:
    for key, value in (headers or DEFAULT_SECURITY_HEADERS).items():
        response.headers.setdefault(key, value)


def install_request_hardening(
    app: FastAPI,
    *,
    skip_paths: Iterable[str] = ("/healthz",),
    security_headers: dict[str, str] | None = None,
) -> None:
    """Install lightweight request hardening middleware.

    Adds a stable request id, response timing, and conservative security headers.
    This intentionally avoids CSP because the current Sophia UI is an inline
    HTML/CSS/JS page; CSP should be introduced after the UI is split into static
    assets.
    """
    skip = set(skip_paths)
    headers = security_headers or DEFAULT_SECURITY_HEADERS

    @app.middleware("http")
    async def request_hardening_middleware(request: Request, call_next: Callable):
        request_id = make_request_id(request.headers.get("X-Request-ID"))
        request.state.request_id = request_id
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            raise
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-ms"] = f"{elapsed_ms:.2f}"
        if request.url.path not in skip:
            apply_security_headers(response, headers=headers)
        return response
