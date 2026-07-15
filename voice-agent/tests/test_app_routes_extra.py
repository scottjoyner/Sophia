from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from voice_agent.config import AppConfig, PathsConfig, load_config
from voice_agent.server.app import create_app
from voice_agent.util.audio import write_wav


def _make_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, override: bool = False) -> TestClient:
    monkeypatch.setenv("SOPHIA_APP_PASSWORD", "test-pass")
    monkeypatch.setenv("SOPHIA_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("SOPHIA_OWNER_OVERRIDE_TOKEN", "test-admin-key")
    if override:
        monkeypatch.setenv("SOPHIA_OWNER_OVERRIDE_ENABLED", "true")
    config = AppConfig(
        paths=PathsConfig(
            artifacts_dir=str(tmp_path / "runs"),
            workspace_dir=str(tmp_path / "workspace"),
            capture_dir=str(tmp_path / "captures"),
        )
    )
    config.auth.owner_override_enabled = override
    config.auth.owner_override_token = "test-admin-key"
    return TestClient(create_app(config))


def _wav_bytes(path: Path, seconds: float = 3.0, sr: int = 16000, freq: float = 220.0) -> bytes:
    n = int(seconds * sr)
    samples = (0.4 * np.sin(2 * np.pi * freq * np.arange(n) / sr)).astype(np.float32)
    write_wav(path, samples, sr)
    return path.read_bytes()


def _enroll_alice(client, tmp_path):
    wav = _wav_bytes(tmp_path / "voice.wav")
    return client.post("/voiceprints/enroll", files={"audio": ("voice.wav", wav, "audio/wav")}, data={"user_id": "alice"})


def _login(client):
    r = client.post("/auth/login", json={"passphrase": "test-pass"})
    assert r.status_code == 200
    return client


def test_owner_override_enroll_requires_override_enabled(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch, override=False)
    wav = _wav_bytes(tmp_path / "voice.wav")
    r = client.post(
        "/voiceprints/owner-override-enroll",
        files={"audio": ("voice.wav", wav, "audio/wav")},
        data={"user_id": "scott", "admin_key": "test-admin-key"},
    )
    assert r.status_code == 403


def test_owner_override_enroll_wrong_key(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch, override=True)
    wav = _wav_bytes(tmp_path / "voice.wav")
    r = client.post(
        "/voiceprints/owner-override-enroll",
        files={"audio": ("voice.wav", wav, "audio/wav")},
        data={"user_id": "scott", "admin_key": "wrong"},
    )
    assert r.status_code == 403


def test_owner_override_enroll_success(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch, override=True)
    wav = _wav_bytes(tmp_path / "voice.wav")
    r = client.post(
        "/voiceprints/owner-override-enroll",
        files={"audio": ("voice.wav", wav, "audio/wav")},
        data={"user_id": "scott", "admin_key": "test-admin-key"},
    )
    assert r.status_code == 200
    assert r.json()["sample_count"] == 1


def test_link_speakers_no_graph_returns_400(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch, override=True)
    client = _login(client)
    wav = _wav_bytes(tmp_path / "voice.wav")
    client.post(
        "/voiceprints/owner-override-enroll",
        files={"audio": ("voice.wav", wav, "audio/wav")},
        data={"user_id": "scott", "admin_key": "test-admin-key"},
    )
    r = client.post("/voiceprints/link-speakers", data={"user_id": "scott", "admin_key": "test-admin-key"})
    assert r.status_code == 400
    assert "Neo4j" in r.json()["detail"]


def test_backfill_no_graph_returns_400(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch, override=True)
    client = _login(client)
    r = client.post("/voiceprints/backfill-global-speakers", data={"admin_key": "test-admin-key"})
    assert r.status_code == 400


def test_reconcile_no_graph_returns_400(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch, override=True)
    client = _login(client)
    r = client.post("/voiceprints/reconcile", data={"admin_key": "test-admin-key"})
    assert r.status_code == 400


def test_train_neo4j_requires_session(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    r = client.post("/voiceprints/train-neo4j", json={"user_id": "alice"})
    assert r.status_code == 401


def test_train_neo4j_requires_neo4j_password(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    client = _login(client)
    r = client.post("/voiceprints/train-neo4j", json={"user_id": "alice"})
    assert r.status_code == 400


def test_voiceprint_admin_endpoints_require_session(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    cases = [
        ("/voiceprints/link-speakers", {"data": {"user_id": "scott"}}),
        ("/voiceprints/backfill-global-speakers", {"data": {}}),
        ("/voiceprints/reconcile", {"data": {}}),
        ("/voiceprints/train-neo4j", {"json": {"user_id": "alice"}}),
    ]
    for path, kwargs in cases:
        r = client.post(path, **kwargs)
        assert r.status_code == 401, path


def test_reconcile_status_requires_session(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    assert client.get("/voiceprints/reconcile/status").status_code == 401


def test_reconcile_status_reports_no_graph(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch, override=True)
    client = _login(client)
    r = client.get("/voiceprints/reconcile/status", params={"admin_key": "test-admin-key"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["check_only"] is True


def test_destructive_endpoints_require_session(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    assert client.delete("/voiceprints/device/alice/phone").status_code == 401
    assert client.delete("/meeting/history/m1").status_code == 401


def test_voiceprints_status_lists_enrolled(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    _enroll_alice(client, tmp_path)
    data = client.get("/voiceprints/status").json()
    assert data["count"] >= 1
    assert any(u["user_id"] == "alice" for u in data["users"])


def test_delete_device_missing_returns_ok_false(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    client = _login(client)
    r = client.delete("/voiceprints/device/alice/phone")
    assert r.status_code == 200
    assert r.json()["deleted"] is False


def test_meeting_process_requires_audio(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    assert client.post("/meeting/process").status_code == 400


def test_meeting_process_and_status(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    wav = _wav_bytes(tmp_path / "meeting.wav", seconds=1.0)
    r = client.post("/meeting/process", files={"audio": ("meeting.wav", wav, "audio/wav")})
    assert r.status_code == 200
    task_id = r.json()["task_id"]
    status = client.get(f"/meeting/status/{task_id}").json()
    assert status["task_id"] == task_id


def test_meeting_status_unknown_404(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    assert client.get("/meeting/status/nope").status_code == 404


def test_meeting_history_and_detail_no_neo4j(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    client = _login(client)
    assert client.get("/meeting/history").json()["meetings"] == []
    assert "error" in client.get("/meeting/history/m1").json()
    assert client.delete("/meeting/history/m1").status_code == 400


def test_dispatch_status_keys(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ASSISTX_VOICE_WEBHOOK_BASE_URL", "http://127.0.0.1:1")
    client = _make_client(tmp_path, monkeypatch)
    data = client.get("/dispatch/status").json()
    assert "assistx_reachable" in data
    assert "dispatched_count" in data


def test_dispatch_to_assistx_route(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    client = _login(client)
    r = client.post("/dispatch/to-assistx", json={"event_type": "task_created", "text": "do thing"})
    assert r.status_code == 200
    assert "event_id" in r.json()


def test_dispatch_to_assistx_requires_session(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    r = client.post("/dispatch/to-assistx", json={"event_type": "task_created", "text": "do thing"})
    assert r.status_code == 401


def test_enroll_requires_token_when_configured(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SOPHIA_ENROLLMENT_TOKEN", "enroll-secret")
    config = load_config(None)
    config.paths = config.paths.model_copy(
        update={
            "artifacts_dir": str(tmp_path / "runs"),
            "workspace_dir": str(tmp_path / "workspace"),
            "capture_dir": str(tmp_path / "captures"),
        }
    )
    client = TestClient(create_app(config))
    wav = _wav_bytes(tmp_path / "voice.wav")
    files = {"audio": ("voice.wav", wav, "audio/wav")}
    r = client.post("/voiceprints/enroll", files=files, data={"user_id": "alice"})
    assert r.status_code == 401
    r2 = client.post(
        "/voiceprints/enroll", files=files, data={"user_id": "alice", "enrollment_token": "enroll-secret"}
    )
    assert r2.status_code == 200


def test_dispatch_trace_returns_correlation(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ASSISTX_VOICE_WEBHOOK_BASE_URL", "http://127.0.0.1:1")
    client = _make_client(tmp_path, monkeypatch)
    data = client.get("/dispatch/trace/abc123").json()
    assert data["correlation_id"] == "abc123"


def test_voice_login_accepts_enrolled(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    wav = _wav_bytes(tmp_path / "voice.wav")
    _enroll_alice(client, tmp_path)
    r = client.post(
        "/auth/voice-login",
        files={"audio": ("voice.wav", wav, "audio/wav")},
        data={"user_id": "alice"},
    )
    assert r.status_code == 200
    assert r.json()["authenticated"] is True


def test_voice_login_rejects_unknown(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    wav = _wav_bytes(tmp_path / "voice.wav")
    r = client.post(
        "/auth/voice-login",
        files={"audio": ("voice.wav", wav, "audio/wav")},
        data={"user_id": "nobody"},
    )
    assert r.status_code == 401
    assert r.json()["authenticated"] is False


def test_enroll_silent_clip_rejected(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    silent = tmp_path / "silent.wav"
    write_wav(silent, np.zeros(16000 * 3, dtype=np.float32), 16000)
    r = client.post(
        "/voiceprints/enroll",
        files={"audio": ("silent.wav", silent.read_bytes(), "audio/wav")},
        data={"user_id": "alice"},
    )
    assert r.status_code == 422


def test_capture_with_audio_upload(tmp_path, monkeypatch) -> None:
    client = _make_client(tmp_path, monkeypatch)
    wav = _wav_bytes(tmp_path / "cap.wav", seconds=1.0)
    r = client.post(
        "/capture",
        files={"audio": ("cap.wav", wav, "audio/wav")},
        data={"user_id": "alice", "transcript": ""},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["bytes"] > 0
