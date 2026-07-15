from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from voice_agent.config import AppConfig, PathsConfig
from voice_agent.server.app import create_app
from voice_agent.util.audio import write_wav


def _make_wav(path: Path, seconds: float = 3.0, sr: int = 16000, freq: float = 220.0) -> bytes:
    n = int(seconds * sr)
    samples = (0.4 * np.sin(2 * np.pi * freq * np.arange(n) / sr)).astype(np.float32)
    write_wav(path, samples, sr)
    return path.read_bytes()


def _make_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, password: str = "test-pass") -> TestClient:
    monkeypatch.setenv("SOPHIA_APP_PASSWORD", password)
    monkeypatch.setenv("SOPHIA_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("SOPHIA_OWNER_OVERRIDE_TOKEN", "test-admin-key")
    config = AppConfig(
        paths=PathsConfig(
            artifacts_dir=str(tmp_path / "runs"),
            workspace_dir=str(tmp_path / "workspace"),
            capture_dir=str(tmp_path / "captures"),
        )
    )
    return TestClient(create_app(config))


def test_healthz(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_readyz(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_status_keys(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    data = client.get("/status").json()
    assert "llm" in data
    assert "stt" in data
    assert "memory_graph" in data
    assert "voiceprint_override" in data


def test_memory_graph_status(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    data = client.get("/memory-graph/status").json()
    assert "password_configured" in data


def test_llm_test(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    data = client.post("/llm/test").json()
    assert "provider" in data


def test_events_endpoint(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    data = client.get("/events").json()
    assert "events" in data


def test_intent_endpoint(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    data = client.post("/intent", json={"transcript": "open the door"}).json()
    assert data["intent"] in {"command", "dictation", "question", "chat"}


def test_voice_chat_endpoint(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    data = client.post("/voice-chat", json={"transcript": "what time is it", "session_id": "s", "user_id": "u"}).json()
    assert "response" in data


def test_capture_without_audio(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    r = client.post("/capture", data={"transcript": "remember the milk", "user_id": "alice"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["transcript"] == "remember the milk"
    assert body["graph_saved"] is False


def test_captures_requires_session_and_neo4j(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    assert client.get("/captures").status_code == 401
    client.post("/auth/login", json={"passphrase": "test-pass"})
    data = client.get("/captures").json()
    assert data["ok"] is False


def test_voiceprints_linkage(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    data = client.get("/voiceprints/linkage").json()
    assert "link_enabled" in data
    assert "linked_speakers" in data


def test_enroll_and_verify_voiceprint(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    wav_path = tmp_path / "voice.wav"
    wav_bytes = _make_wav(wav_path)
    enroll = client.post(
        "/voiceprints/enroll",
        files={"audio": ("voice.wav", wav_bytes, "audio/wav")},
        data={"user_id": "alice"},
    )
    assert enroll.status_code == 200
    assert enroll.json()["sample_count"] == 1

    verify = client.post(
        "/auth/verify",
        files={"audio": ("voice.wav", wav_bytes, "audio/wav")},
        data={"user_id": "alice"},
    )
    assert verify.status_code == 200
    assert verify.json()["accepted"] is True


def test_auth_verify_unknown_user(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    wav_path = tmp_path / "voice.wav"
    wav_bytes = _make_wav(wav_path)
    verify = client.post(
        "/auth/verify",
        files={"audio": ("voice.wav", wav_bytes, "audio/wav")},
        data={"user_id": "nobody"},
    )
    assert verify.status_code == 200
    assert verify.json()["accepted"] is False


def test_login_logout_session(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    assert client.get("/auth/session").json()["authenticated"] is False
    assert client.post("/auth/login", json={"passphrase": "test-pass"}).status_code == 200
    assert client.get("/auth/session").json()["authenticated"] is True
    assert client.post("/auth/logout").status_code == 200
    assert client.get("/auth/session").json()["authenticated"] is False
