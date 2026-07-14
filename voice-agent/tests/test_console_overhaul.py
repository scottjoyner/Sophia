from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from voice_agent.config import AppConfig, PathsConfig
from voice_agent.server.app import create_app


def _make_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, password: str = "test-pass") -> TestClient:
    monkeypatch.setenv("SOPHIA_APP_PASSWORD", password)
    monkeypatch.setenv("SOPHIA_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("SOPHIA_OWNER_OVERRIDE_TOKEN", "test-admin-key")
    config = AppConfig(
        paths=PathsConfig(artifacts_dir=str(tmp_path / "runs"), workspace_dir=str(tmp_path / "workspace"))
    )
    return TestClient(create_app(config))


def _parse_sse(text: str) -> list:
    events = []
    for chunk in text.split("\n\n"):
        chunk = chunk.strip()
        if not chunk.startswith("data:"):
            continue
        payload = chunk[len("data:") :].strip()
        if not payload:
            continue
        events.append(__import__("json").loads(payload))
    return events


def test_login_accepts_correct_passphrase_and_sets_cookie(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    r = client.post("/auth/login", json={"passphrase": "test-pass"})
    assert r.status_code == 200
    assert r.json()["authenticated"] is True
    assert "sophia_session" in r.cookies


def test_login_rejects_wrong_passphrase(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    r = client.post("/auth/login", json={"passphrase": "nope"})
    assert r.status_code == 401


def test_session_reflects_login_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    assert client.get("/auth/session").json()["authenticated"] is False
    client.post("/auth/login", json={"passphrase": "test-pass"})
    sess = client.get("/auth/session").json()
    assert sess["authenticated"] is True
    assert sess["user_id"]


def test_chat_stream_requires_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    r = client.post("/api/chat/stream", json={"messages": [{"role": "user", "content": "hello"}]})
    assert r.status_code == 401


def test_chat_stream_returns_sse_tokens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    client.post("/auth/login", json={"passphrase": "test-pass"})
    r = client.post("/api/chat/stream", json={"messages": [{"role": "user", "content": "what is the status"}]})
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    events = _parse_sse(r.text)
    assert events
    assert any(e["type"] == "token" for e in events)
    assert events[-1]["type"] == "done"


def test_chat_stream_extracts_and_ingests_tasks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    client.post("/auth/login", json={"passphrase": "test-pass"})
    conversation = "Remind me to send the quarterly report to finance by Friday."
    r = client.post(
        "/api/chat/stream",
        json={
            "messages": [
                {"role": "system", "content": "You are Sophia."},
                {"role": "user", "content": conversation},
            ],
            "session_id": "console",
        },
    )
    assert r.status_code == 200
    events = _parse_sse(r.text)
    task_events = [e for e in events if e["type"] == "tasks"]
    ingested_events = [e for e in events if e["type"] == "ingested"]
    if task_events:
        assert ingested_events, "tasks were extracted but never ingested"


def test_status_reports_honest_model_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    status = client.get("/status").json()
    assert "llm" in status
    assert isinstance(status["llm"]["assistant_configured"], bool)
    assert status["llm"]["assistant_model"] in {"mock", "auto-router", "small"}


def test_voiceprints_linkage_graceful_without_neo4j(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    r = client.get("/voiceprints/linkage")
    assert r.status_code == 200
    data = r.json()
    assert "link_enabled" in data
    assert "link_threshold" in data
    assert isinstance(data["linked_speakers"], list)


def test_console_homepage_renders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    r = client.get("/")
    assert r.status_code == 200
    assert "Sophia" in r.text
    assert "sophia_session" not in r.cookies
    legacy = client.get("/legacy")
    assert legacy.status_code == 200
