from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from collections import OrderedDict
from typing import Any, Dict, Optional

import httpx


ASSISTX_VOICE_WEBHOOK_BASE_URL = os.getenv("ASSISTX_VOICE_WEBHOOK_BASE_URL", "http://host.docker.internal:8000").rstrip("/")
ASSISTX_VOICE_WEBHOOK_BASE_URL_CONFIGURED = "ASSISTX_VOICE_WEBHOOK_BASE_URL" in os.environ
ASSISTX_VOICE_WEBHOOK_SECRET = (
    os.getenv("ASSISTX_VOICE_WEBHOOK_SECRET") or os.getenv("VOICE_WEBHOOK_SECRET") or ""
).strip()
ASSISTX_VOICE_WEBHOOK_SECRET_CONFIGURED = bool(
    os.getenv("ASSISTX_VOICE_WEBHOOK_SECRET") or os.getenv("VOICE_WEBHOOK_SECRET")
)


def assistx_base_url(raw: Optional[str] = None) -> str:
    candidate = (
        ASSISTX_VOICE_WEBHOOK_BASE_URL
        if ASSISTX_VOICE_WEBHOOK_BASE_URL_CONFIGURED
        else (raw or ASSISTX_VOICE_WEBHOOK_BASE_URL or "")
    ).strip().rstrip("/")
    if candidate.endswith("/api/voice/events"):
        candidate = candidate[: -len("/api/voice/events")].rstrip("/")
    return candidate or "http://host.docker.internal:8000"


def assistx_webhook_url(raw: Optional[str] = None) -> str:
    return f"{assistx_base_url(raw)}/api/voice/events"


def assistx_basic_auth() -> Optional[tuple[str, str]]:
    user = os.getenv("ASSISTX_BASIC_AUTH_USER", "")
    password = os.getenv("ASSISTX_BASIC_AUTH_PASS", "")
    if user and password:
        return (user, password)
    return None


def build_voice_event(
    event_type: str,
    text: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    *,
    session_id: Optional[str] = None,
    auto_dispatch: bool = True,
    actor: Optional[Dict[str, Any]] = None,
) -> "OrderedDict[str, Any]":
    event_id = uuid.uuid4().hex
    correlation_id = uuid.uuid4().hex
    dispatch_id = uuid.uuid4().hex
    now = str(time.time())
    meta = {k: v for k, v in (metadata or {}).items() if v is not None} or None
    actor = actor or {}
    payload: "OrderedDict[str, Any]" = OrderedDict()
    payload["event_id"] = event_id
    payload["event_type"] = event_type
    if text:
        payload["text"] = text
    payload["source"] = "sophia_voice"
    payload["schema_version"] = "2026-06-08.v1"
    payload["correlation_id"] = correlation_id
    if session_id:
        payload["session_id"] = session_id
    payload["client_ts"] = now
    if meta is not None:
        payload["metadata"] = meta
    payload["actor"] = {
        "user_id": actor.get("user_id", "scott"),
        "device_id": actor.get("device_id"),
        "auth_state": actor.get("auth_state", "accepted" if (meta or {}).get("accepted") else "not_required"),
    }
    payload["links"] = {
        "correlation_id": correlation_id,
        "dispatch_id": dispatch_id,
        "task_id": None,
        "route_id": None,
        "assignment_id": None,
    }
    payload["auto_dispatch"] = auto_dispatch
    return payload


def sign_headers(token: str, body_bytes: bytes) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    user, password = (os.getenv("ASSISTX_BASIC_AUTH_USER", ""), os.getenv("ASSISTX_BASIC_AUTH_PASS", ""))
    if user and password:
        import base64

        creds = base64.b64encode(f"{user}:{password}".encode()).decode()
        headers["Authorization"] = f"Basic {creds}"
    else:
        sig = hmac.new(token.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
        headers["X-Voice-Signature"] = f"sha256={sig}"
    return headers


def dispatch_to_assistx(
    payload: Dict[str, Any],
    *,
    target_url: Optional[str] = None,
    token: Optional[str] = None,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    webhook_url = assistx_webhook_url(target_url)
    secret = ASSISTX_VOICE_WEBHOOK_SECRET or (token or "").strip()
    event_id = payload.get("event_id")
    correlation_id = payload.get("correlation_id")
    event_type = payload.get("event_type")
    body_bytes = json_dumps(payload)
    headers = sign_headers(secret, body_bytes) if secret else {"Content-Type": "application/json"}
    result: Dict[str, Any] = {
        "event_id": event_id,
        "sent": False,
        "error": None,
        "response": None,
        "correlation_id": correlation_id,
    }
    if not secret:
        result["error"] = "Voice webhook secret not configured"
        return result
    try:
        r = httpx.post(webhook_url, content=body_bytes, headers=headers, timeout=timeout)
        result["sent"] = r.status_code == 200
        if r.status_code == 200:
            try:
                resp_data = r.json()
            except Exception:
                resp_data = {}
            result["response"] = resp_data
            result["correlation_id"] = resp_data.get("correlation_id", correlation_id)
            result["task_id"] = resp_data.get("task_id")
            result["dispatch_id"] = resp_data.get("dispatch_id")
            result["intent_id"] = resp_data.get("intent_id")
        else:
            result["error"] = f"HTTP {r.status_code}: {r.text[:300]}"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def json_dumps(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
