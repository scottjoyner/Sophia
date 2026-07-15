from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

from fastapi import HTTPException, Request

SESSION_COOKIE = "sophia_session"
SESSION_TTL_SECONDS = 60 * 60 * 12  # 12 hours


def _app_secret() -> str:
    return (
        os.getenv("SOPHIA_SESSION_SECRET")
        or os.getenv("SOPHIA_OWNER_OVERRIDE_TOKEN")
        or "insecure-dev-secret-change-me"
    )


def _app_password() -> str:
    return (
        os.getenv("SOPHIA_APP_PASSWORD")
        or os.getenv("SOPHIA_OWNER_OVERRIDE_TOKEN")
        or "sophia"
    )


def create_session_token() -> str:
    session_id = secrets.token_hex(16)
    expires = int(time.time()) + SESSION_TTL_SECONDS
    body = f"{session_id}.{expires}"
    sig = hmac.new(_app_secret().encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_session_token(token: str | None) -> bool:
    if not token or token.count(".") != 2:
        return False
    try:
        session_id, expires, sig = token.split(".")
        if int(expires) < int(time.time()):
            return False
    except ValueError:
        return False
    body = f"{session_id}.{expires}"
    expected = hmac.new(_app_secret().encode(), body.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def login(passphrase: str) -> str | None:
    expected = _app_password()
    if not expected:
        return None
    if hmac.compare_digest(passphrase, expected):
        return create_session_token()
    return None


def require_session(request: Request) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if not verify_session_token(token):
        raise HTTPException(status_code=401, detail="Not authenticated")
