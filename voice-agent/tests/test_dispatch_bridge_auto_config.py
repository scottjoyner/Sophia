from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from voice_agent.config import AppConfig, PathsConfig
from voice_agent.server.app import create_app


class _Response:
    def __init__(self, status_code: int = 200, body: dict | None = None, text: str = "ok") -> None:
        self.status_code = status_code
        self._body = body or {"ok": True}
        self.text = text

    def json(self) -> dict:
        return self._body


def _app(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.setattr("fastapi.dependencies.utils.ensure_multipart_is_installed", lambda: None)
    config = AppConfig(paths=PathsConfig(artifacts_dir=str(tmp_path / "runs"), workspace_dir=str(tmp_path / "workspace")))
    return TestClient(create_app(config))


def test_dispatch_to_assistx_uses_env_base_and_secret(monkeypatch, tmp_path):
    client = _app(monkeypatch, tmp_path)
    captured: dict[str, object] = {}

    monkeypatch.setattr("voice_agent.server.app.ASSISTX_VOICE_WEBHOOK_BASE_URL", "http://assistant.local:8000")
    monkeypatch.setattr("voice_agent.server.app.ASSISTX_VOICE_WEBHOOK_BASE_URL_CONFIGURED", True)
    monkeypatch.setattr("voice_agent.server.app.ASSISTX_VOICE_WEBHOOK_SECRET", "env-secret-123")

    def fake_post(url, content=None, headers=None, timeout=None):
        captured["url"] = url
        captured["content"] = content
        captured["headers"] = headers or {}
        return _Response(200, {"signal_event_id": "evt-1"})

    monkeypatch.setattr(httpx, "post", fake_post)

    payload = {
        "event_type": "voice_auth",
        "text": "check the voice bridge",
        "metadata": {"debug": True},
        "target_url": "http://bad.example:1234",
        "target_token": "wrong-token",
        "auto_dispatch": True,
    }
    response = client.post("/dispatch/to-assistx", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sent"] is True
    assert body["error"] is None

    assert captured["url"] == "http://assistant.local:8000/api/voice/events"
    content = captured["content"]
    assert isinstance(content, (bytes, bytearray))
    headers = captured["headers"]
    assert headers["Content-Type"] == "application/json"
    assert headers["X-Voice-Signature"].startswith("sha256=")

    expected = hmac.new(b"env-secret-123", content, hashlib.sha256).hexdigest()
    assert headers["X-Voice-Signature"] == f"sha256={expected}"

    sent_payload = json.loads(content.decode("utf-8"))
    assert sent_payload["event_type"] == "voice_auth"
    assert sent_payload["source"] == "sophia_voice"
    assert sent_payload["metadata"] == {"debug": True}


def test_dispatch_status_uses_env_base(monkeypatch, tmp_path):
    client = _app(monkeypatch, tmp_path)
    monkeypatch.setattr("voice_agent.server.app.ASSISTX_VOICE_WEBHOOK_BASE_URL", "http://assistant.local:8000")
    monkeypatch.setattr("voice_agent.server.app.ASSISTX_VOICE_WEBHOOK_BASE_URL_CONFIGURED", True)

    def fake_post(url, content=None, headers=None, timeout=None):
        assert url == "http://assistant.local:8000/api/voice/events"
        assert content == b"{}"
        assert headers and headers["Content-Type"] == "application/json"
        return _Response(401, {"detail": "Missing voice signature header"}, text='{"detail":"Missing voice signature header"}')

    monkeypatch.setattr(httpx, "post", fake_post)

    response = client.get("/dispatch/status")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["assistx_reachable"] is True
    assert body["assistx_webhook_ok"] is False
    assert body["assistx_webhook_status"] == 401
    assert body["assistx_url"] == "http://assistant.local:8000"
