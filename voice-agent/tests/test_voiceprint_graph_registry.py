from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from voice_agent.auth.registry import VoiceprintRegistry
from voice_agent.auth.verify import verify_audio_segment
from voice_agent.config import AppConfig, PathsConfig
from voice_agent.server.app import create_app


class FakeGraphStore:
    def __init__(self) -> None:
        self.records: dict[str, list[dict[str, object]]] = {}
        self.candidates: dict[str, list[dict[str, object]]] = {}
        self.deleted: list[tuple[str, str]] = []

    def save_voiceprint(self, **kwargs):
        user_id = str(kwargs["user_id"])
        device_id = kwargs.get("device_id") or None
        scope = "device" if device_id else "identity"
        group_key = f"{user_id}:device:{device_id}" if device_id else f"{user_id}:identity"
        record = {
            "user_id": user_id,
            "group_key": group_key,
            "scope": scope,
            "device_id": device_id,
            "version_id": f"{user_id}-{device_id or 'default'}-v1",
            "embedding": list(kwargs["embedding_mean"]),
            "samples": kwargs["samples"],
            "threshold": kwargs["threshold"],
            "sample_count": len((kwargs["samples"] or {}).get("samples") or []),
            "source": kwargs.get("source"),
            "append": kwargs.get("append", False),
            "lineage_mode": "initial",
            "active": True,
            "captured_in_graph": True,
        }
        self.records.setdefault(user_id, [])
        self.records[user_id] = [r for r in self.records[user_id] if r.get("device_id") != device_id]
        self.records[user_id].append(record)
        return record

    def get_active_records(self, user_id: str):
        return list(self.records.get(user_id, []))

    def get_active_record(self, user_id: str):
        for record in self.records.get(user_id, []):
            if record.get("scope") == "identity":
                return record
        return next(iter(self.records.get(user_id, [])), None)

    def get_device_record(self, user_id: str, device_id: str):
        for record in self.records.get(user_id, []):
            if record.get("device_id") == device_id:
                return record
        return None

    def list_user_ids(self):
        return sorted(self.records.keys())

    def delete_device_voiceprint(self, user_id: str, device_id: str) -> bool:
        self.deleted.append((user_id, device_id))
        before = len(self.records.get(user_id, []))
        self.records[user_id] = [r for r in self.records.get(user_id, []) if r.get("device_id") != device_id]
        return len(self.records.get(user_id, [])) != before

    def get_historical_candidates(self, user_id: str):
        return list(self.candidates.get(user_id, []))

    def search_candidates(self, user_id: str, embedding, top_k: int = 5):
        return list(self.candidates.get(user_id, []))[:top_k]


def _sample_samples() -> dict[str, object]:
    return {
        "samples": [
            {
                "path": "/tmp/a.wav",
                "sha256": "abc",
                "source": "unit-test",
                "sample_rate": 16000,
                "duration_seconds": 2.5,
                "energy": 0.12,
                "embedding": [0.1, 0.2, 0.3],
                "added_ts_ms": 1,
            }
        ],
        "sample_count": 1,
        "source": "unit-test",
    }


def test_registry_prefers_graph_records(tmp_path: Path) -> None:
    registry = VoiceprintRegistry(tmp_path / "results.sqlite")
    graph = FakeGraphStore()
    registry.graph = graph

    graph.save_voiceprint(
        user_id="scott",
        embedding_mean=[0.1, 0.2, 0.3],
        samples=_sample_samples(),
        threshold=0.75,
        source="seed",
        append=False,
    )
    graph.save_voiceprint(
        user_id="scott",
        embedding_mean=[0.4, 0.5, 0.6],
        samples=_sample_samples(),
        threshold=0.75,
        source="seed",
        append=False,
        device_id="phone",
    )

    base = registry.get("scott")
    assert base is not None
    assert base["version_id"] == "scott-default-v1"
    assert base["device_id"] is None

    devices = registry.get_devices("scott")
    assert list(devices.keys()) == ["phone"]
    assert devices["phone"]["version_id"] == "scott-phone-v1"

    all_records = registry.get_all_for_user("scott")
    assert {record["device_id"] for record in all_records} == {"default", "phone"}
    assert registry.list_users() == ["scott"]

    assert registry.delete_device("scott", "phone") is True
    assert registry.get_devices("scott") == {}


def test_verify_uses_graph_records(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = AppConfig(paths=PathsConfig(artifacts_dir=str(tmp_path / "runs"), workspace_dir=str(tmp_path / "workspace")))
    registry = VoiceprintRegistry(Path(config.paths.artifacts_dir) / "results.sqlite")
    graph = FakeGraphStore()
    registry.graph = graph
    graph.save_voiceprint(
        user_id="scott",
        embedding_mean=[1.0, 0.0, 0.0],
        samples=_sample_samples(),
        threshold=0.5,
        source="seed",
        append=False,
    )

    class FakeEmbedder:
        def embed(self, samples: np.ndarray, sample_rate: int):
            return [1.0, 0.0, 0.0]

    monkeypatch.setattr("voice_agent.auth.verify.VoiceprintRegistry", lambda path, config=None: registry)
    monkeypatch.setattr("voice_agent.auth.verify.SpeakerEmbedder", FakeEmbedder)

    result = verify_audio_segment(config, "session-1", "scott", np.array([0.2, 0.1, 0.0], dtype=float), 16000)
    assert result["accepted"] is True
    assert result["voiceprint_version_id"] == "scott-default-v1"
    assert result["voiceprint_scope"] == "identity"


def test_verify_falls_back_to_historical_candidates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = AppConfig(paths=PathsConfig(artifacts_dir=str(tmp_path / "runs"), workspace_dir=str(tmp_path / "workspace")))
    registry = VoiceprintRegistry(Path(config.paths.artifacts_dir) / "results.sqlite")
    graph = FakeGraphStore()
    registry.graph = graph
    graph.save_voiceprint(
        user_id="scott",
        embedding_mean=[0.0, 1.0, 0.0],
        samples=_sample_samples(),
        threshold=0.95,
        source="seed",
        append=False,
    )
    graph.candidates["scott"] = [
        {
            "user_id": "scott",
            "group_key": "scott:identity",
            "scope": "identity",
            "device_id": None,
            "version_id": "scott-fallback-v2",
            "candidate_id": "version:scott-fallback-v2",
            "candidate_type": "version",
            "embedding": [1.0, 0.0, 0.0],
            "threshold": 0.6,
            "sample_count": 2,
            "source": "historical",
            "append": False,
            "lineage_mode": "append",
            "active": False,
        }
    ]

    class FakeEmbedder:
        def embed(self, samples: np.ndarray, sample_rate: int):
            return [1.0, 0.0, 0.0]

    monkeypatch.setattr("voice_agent.auth.verify.VoiceprintRegistry", lambda path, config=None: registry)
    monkeypatch.setattr("voice_agent.auth.verify.SpeakerEmbedder", FakeEmbedder)

    result = verify_audio_segment(config, "session-2", "scott", np.array([0.2, 0.1, 0.0], dtype=float), 16000)
    assert result["accepted"] is True
    assert result["match_source"] == "historical_fallback"
    assert result["voiceprint_version_id"] == "scott-fallback-v2"
    assert result["fallback_used"] is True
    assert result["voiceprint_candidate_ids"] == ["version:scott-fallback-v2"]


def test_voiceprint_status_and_delete_use_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = AppConfig(paths=PathsConfig(artifacts_dir=str(tmp_path / "runs"), workspace_dir=str(tmp_path / "workspace")))
    registry = VoiceprintRegistry(Path(config.paths.artifacts_dir) / "results.sqlite", config)
    graph = FakeGraphStore()
    registry.graph = graph
    graph.save_voiceprint(
        user_id="scott",
        embedding_mean=[1.0, 0.0, 0.0],
        samples=_sample_samples(),
        threshold=0.5,
        source="seed",
        append=False,
    )
    graph.save_voiceprint(
        user_id="scott",
        embedding_mean=[0.9, 0.1, 0.0],
        samples=_sample_samples(),
        threshold=0.5,
        source="seed",
        append=False,
        device_id="phone",
    )

    monkeypatch.setattr("voice_agent.server.app.VoiceprintRegistry", lambda path, config=None: registry)
    monkeypatch.setenv("SOPHIA_APP_PASSWORD", "sophia")
    monkeypatch.setenv("SOPHIA_SESSION_SECRET", "test-secret")
    client = TestClient(create_app(config))
    client.post("/auth/login", json={"passphrase": "sophia"})

    status = client.get("/voiceprints/status").json()
    assert status["count"] == 1
    assert status["users"][0]["version_id"] == "scott-default-v1"
    assert "phone" in status["users"][0]["devices"]

    deleted = client.delete("/voiceprints/device/scott/phone").json()
    assert deleted["deleted"] is True
    assert registry.get_devices("scott") == {}
