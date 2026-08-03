from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from collections import OrderedDict
from typing import Any

import httpx

from ..contracts_shim import (
    ADMIN_VOICE_OVERRIDE,
    AUTHENTICATED_SCOTT,
    REGISTERED_USER_UNVERIFIED,
    REJECTED,
    UNKNOWN_SPEAKER,
)

ASSISTX_VOICE_WEBHOOK_BASE_URL = os.getenv(
    "ASSISTX_VOICE_WEBHOOK_BASE_URL",
    "http://host.docker.internal:8000",
).rstrip("/")
ASSISTX_VOICE_WEBHOOK_BASE_URL_CONFIGURED = (
    "ASSISTX_VOICE_WEBHOOK_BASE_URL" in os.environ
)
ASSISTX_VOICE_WEBHOOK_SECRET = (
    os.getenv("ASSISTX_VOICE_WEBHOOK_SECRET")
    or os.getenv("VOICE_WEBHOOK_SECRET")
    or ""
).strip()
ASSISTX_VOICE_WEBHOOK_SECRET_CONFIGURED = bool(
    os.getenv("ASSISTX_VOICE_WEBHOOK_SECRET")
    or os.getenv("VOICE_WEBHOOK_SECRET")
)

# These are the only current contract states allowed to create an executable
# voice task without a human review boundary. Authorization is intentionally
# separate from speaker detection confidence and from TTS voice selection.
TRUSTED_AUTO_DISPATCH_STATES = {
    AUTHENTICATED_SCOTT,
    ADMIN_VOICE_OVERRIDE,
}

# auto-assist currently creates tasks inline for these legacy event names.
# Untrusted speakers must never reach those event names until the server-side
# authorization gate is canonicalized.
ACTION_EVENT_TYPES = {
    "task_created",
    "meeting_transcript",
}


def assistx_base_url(raw: str | None = None) -> str:
    candidate = (
        ASSISTX_VOICE_WEBHOOK_BASE_URL
        if ASSISTX_VOICE_WEBHOOK_BASE_URL_CONFIGURED
        else (raw or ASSISTX_VOICE_WEBHOOK_BASE_URL or "")
    ).strip().rstrip("/")
    if candidate.endswith("/api/voice/events"):
        candidate = candidate[: -len("/api/voice/events")].rstrip("/")
    return candidate or "http://host.docker.internal:8000"


def assistx_webhook_url(raw: str | None = None) -> str:
    return f"{assistx_base_url(raw)}/api/voice/events"


def assistx_basic_auth() -> tuple[str, str] | None:
    user = os.getenv("ASSISTX_BASIC_AUTH_USER", "")
    password = os.getenv("ASSISTX_BASIC_AUTH_PASS", "")
    if user and password:
        return (user, password)
    return None


def _derive_auth_state(
    metadata: dict[str, Any] | None,
    actor: dict[str, Any],
) -> str:
    explicit = actor.get("auth_state")
    if explicit in {
        AUTHENTICATED_SCOTT,
        UNKNOWN_SPEAKER,
        REGISTERED_USER_UNVERIFIED,
        ADMIN_VOICE_OVERRIDE,
        REJECTED,
    }:
        return str(explicit)
    if (metadata or {}).get("rejected"):
        return REJECTED
    if (metadata or {}).get("admin_override"):
        return ADMIN_VOICE_OVERRIDE
    if (metadata or {}).get("accepted"):
        return AUTHENTICATED_SCOTT
    if actor.get("user_id") and actor["user_id"] != "scott":
        return REGISTERED_USER_UNVERIFIED
    if actor.get("user_id") == "scott":
        return AUTHENTICATED_SCOTT
    return UNKNOWN_SPEAKER


def _actor_user_id(actor: dict[str, Any], auth_state: str) -> str:
    explicit = str(actor.get("user_id") or "").strip()
    if explicit:
        return explicit
    if auth_state in TRUSTED_AUTO_DISPATCH_STATES:
        return "scott"
    return "unknown"


def _authorize_event(
    event_type: str,
    auth_state: str,
    auto_dispatch: bool,
) -> tuple[str, bool, str]:
    """Return the safe event type, dispatch flag, and policy action.

    The current AssistX ``/api/voice/events`` implementation creates tasks for
    ``task_created`` and ``meeting_transcript`` before it has a canonical
    server-side actor authorization gate. Until that endpoint is unified,
    Sophia prevents untrusted voice states from entering those executable event
    paths while still forwarding an auditable review event.
    """

    if event_type not in ACTION_EVENT_TYPES:
        return event_type, auto_dispatch, "not_applicable"
    if auth_state in TRUSTED_AUTO_DISPATCH_STATES:
        action = "auto_dispatch_allowed" if auto_dispatch else "record_only"
        return event_type, auto_dispatch, action
    if auth_state == REJECTED:
        return "voice_action_rejected", False, "rejected"
    if event_type == "meeting_transcript":
        return "meeting_transcript_review", False, "review_required"
    return "task_proposed", False, "review_required"


def build_voice_event(
    event_type: str,
    text: str = "",
    metadata: dict[str, Any] | None = None,
    *,
    session_id: str | None = None,
    auto_dispatch: bool = True,
    actor: dict[str, Any] | None = None,
) -> OrderedDict[str, Any]:
    event_id = uuid.uuid4().hex
    correlation_id = str(uuid.uuid4())
    dispatch_id = uuid.uuid4().hex
    now = str(time.time())
    actor = dict(actor or {})
    auth_state = _derive_auth_state(metadata, actor)
    user_id = _actor_user_id(actor, auth_state)
    safe_event_type, safe_auto_dispatch, authorization_action = _authorize_event(
        event_type,
        auth_state,
        auto_dispatch,
    )

    meta = {k: v for k, v in (metadata or {}).items() if v is not None}
    meta.setdefault("requested_event_type", event_type)
    meta.setdefault("authorization_action", authorization_action)
    meta.setdefault("auth_state", auth_state)

    payload: OrderedDict[str, Any] = OrderedDict()
    payload["event_id"] = event_id
    payload["event_type"] = safe_event_type
    if text:
        payload["text"] = text
    payload["source"] = "sophia_voice"
    payload["schema_version"] = "2026-06-08.v1"
    payload["correlation_id"] = correlation_id
    if session_id:
        payload["session_id"] = session_id
    payload["client_ts"] = now
    payload["metadata"] = meta
    payload["actor"] = {
        "user_id": user_id,
        "device_id": actor.get("device_id"),
        "auth_state": auth_state,
    }
    payload["links"] = {
        "correlation_id": correlation_id,
        "dispatch_id": dispatch_id,
        "task_id": None,
        "route_id": None,
        "assignment_id": None,
    }
    payload["auto_dispatch"] = safe_auto_dispatch
    return payload


def assistx_voice_event_payload(
    payload: dict[str, Any],
) -> OrderedDict[str, Any]:
    """Normalize Sophia's rich local event into AssistX's current wire model.

    AssistX currently parses ``VoiceEventIn(extra='ignore')`` and verifies the
    HMAC against the parsed model's JSON. Sending Sophia-only top-level fields
    therefore changes the signed bytes after parsing. This compatibility
    adapter moves identity and trace fields into metadata and emits fields in
    the same order as the current AssistX model, preserving both HMAC validity
    and end-to-end correlation until the canonical EventEnvelope endpoint is
    adopted server-side.
    """

    actor = payload.get("actor") or {}
    metadata = dict(payload.get("metadata") or {})
    metadata.setdefault("schema_version", payload.get("schema_version"))
    metadata.setdefault("correlation_id", payload.get("correlation_id"))
    metadata.setdefault("user_id", actor.get("user_id"))
    metadata.setdefault("device_id", actor.get("device_id"))
    metadata.setdefault("auth_state", actor.get("auth_state"))
    metadata.setdefault("links", payload.get("links") or {})
    metadata = {key: value for key, value in metadata.items() if value is not None}

    wire: OrderedDict[str, Any] = OrderedDict()
    wire["event_id"] = payload.get("event_id")
    wire["event_type"] = payload.get("event_type")
    if payload.get("text") is not None:
        wire["text"] = payload.get("text")
    wire["source"] = payload.get("source") or "sophia_voice"
    if payload.get("session_id") is not None:
        wire["session_id"] = payload.get("session_id")
    if payload.get("client_ts") is not None:
        wire["client_ts"] = payload.get("client_ts")
    wire["metadata"] = metadata
    wire["auto_dispatch"] = bool(payload.get("auto_dispatch", True))
    return wire


def sign_headers(token: str, body_bytes: bytes) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    user = os.getenv("ASSISTX_BASIC_AUTH_USER", "")
    password = os.getenv("ASSISTX_BASIC_AUTH_PASS", "")
    if user and password:
        import base64

        creds = base64.b64encode(f"{user}:{password}".encode()).decode()
        headers["Authorization"] = f"Basic {creds}"
    else:
        sig = hmac.new(token.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
        headers["X-Voice-Signature"] = f"sha256={sig}"
    return headers


def dispatch_to_assistx(
    payload: dict[str, Any],
    *,
    target_url: str | None = None,
    token: str | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    webhook_url = assistx_webhook_url(target_url)
    secret = ASSISTX_VOICE_WEBHOOK_SECRET or (token or "").strip()
    event_id = payload.get("event_id")
    correlation_id = payload.get("correlation_id")
    wire_payload = assistx_voice_event_payload(payload)
    body_bytes = json_dumps(wire_payload)
    headers = (
        sign_headers(secret, body_bytes)
        if secret
        else {"Content-Type": "application/json"}
    )
    result: dict[str, Any] = {
        "event_id": event_id,
        "sent": False,
        "error": None,
        "response": None,
        "correlation_id": correlation_id,
        "authorization_action": (payload.get("metadata") or {}).get(
            "authorization_action"
        ),
    }
    if not secret:
        result["error"] = "Voice webhook secret not configured"
        return result
    try:
        response = httpx.post(
            webhook_url,
            content=body_bytes,
            headers=headers,
            timeout=timeout,
        )
        result["sent"] = response.status_code == 200
        if response.status_code == 200:
            try:
                response_data = response.json()
            except Exception:
                response_data = {}
            result["response"] = response_data
            result["correlation_id"] = response_data.get(
                "correlation_id",
                correlation_id,
            )
            result["task_id"] = response_data.get("task_id")
            result["dispatch_id"] = response_data.get("dispatch_id")
            result["intent_id"] = response_data.get("intent_id")
        else:
            result["error"] = f"HTTP {response.status_code}: {response.text[:300]}"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def json_dumps(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
