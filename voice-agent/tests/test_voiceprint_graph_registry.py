from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from voice_agent.auth.registry import VoiceprintRegistry
from voice_agent.auth.verify import verify_audio_segment
from voice_agent.auth.voiceprint_graph import VoiceprintGraphStore
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


class _FakeTxResult:
    def __init__(self, rows: list[dict]):
        self._rows = rows
        self._idx = 0

    def single(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    def __init__(self, speaker_match_row=None, owner_match_row=None, global_speaker_row=None):
        self.speaker_match_row = speaker_match_row
        self.owner_match_row = owner_match_row
        self.global_speaker_row = global_speaker_row
        self.run_calls: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, cypher: str, **params):
        self.run_calls.append((cypher, params))
        lowered = cypher.lower()
        if "vector.query" in lowered and "speaker_embedding_idx" in lowered:
            # owner query filters by user_id; speaker query does not
            if "tolower(node.user_id)" in lowered:
                return _FakeTxResult([self.owner_match_row] if self.owner_match_row else [])
            return _FakeTxResult([self.speaker_match_row] if self.speaker_match_row else [])
        if "globalspeaker" in lowered and "tolower(gs.display_label)" in lowered:
            return _FakeTxResult([self.global_speaker_row] if self.global_speaker_row else [])
        return _FakeTxResult([])


class _FakeDriver:
    def __init__(self, session: _FakeSession):
        self._session = session

    def session(self, database=None):
        return self._session

    def close(self):
        return None


def _make_store_with_session(session: _FakeSession) -> VoiceprintGraphStore:
    from voice_agent.auth.voiceprint_graph import VoiceprintGraphStore

    store = VoiceprintGraphStore.__new__(VoiceprintGraphStore)
    store.EMBEDDING_DIMENSION = 3
    store.database = "neo4j"
    store._driver = lambda: _FakeDriver(session)  # type: ignore[assignment]
    return store


def test_link_creates_global_speaker_when_absent() -> None:
    from voice_agent.auth.voiceprint_graph import link_global_speaker_by_label

    session = _FakeSession(global_speaker_row=None)
    result = link_global_speaker_by_label(session, "scott", [0.1, 0.2, 0.3])
    assert result is not None
    assert result["created"] is True
    assert result["global_speaker_id"]
    # A MERGE creating the GlobalSpeaker + the embedding SET must have run.
    writes = [c for c in session.run_calls if "MERGE (gs:GlobalSpeaker" in c[0]]
    assert len(writes) >= 1


def test_link_uses_existing_global_speaker() -> None:
    from voice_agent.auth.voiceprint_graph import link_global_speaker_by_label

    session = _FakeSession(global_speaker_row={"id": "gs-123", "display_label": "scott"})
    result = link_global_speaker_by_label(session, "scott", [0.1, 0.2, 0.3])
    assert result["global_speaker_id"] == "gs-123"
    assert result["created"] is False


def test_foreign_absorption_rejected_when_owner_match_is_better() -> None:
    from voice_agent.auth.voiceprint_graph import VoiceprintGraphStore

    session = _FakeSession(
        # A foreign speaker matches at 0.90 (above threshold)
        speaker_match_row={"speaker_user_id": "intruder", "score": 0.90},
        # But owner's own embedding matches at 0.95 (better) => do NOT absorb
        owner_match_row={"score": 0.95},
        global_speaker_row=None,
    )
    store = _make_store_with_session(session)
    result = store.link_identity_to_global_speakers("scott", [0.1, 0.2, 0.3], match_threshold=0.80)
    assert result["method"] == "embedding_match_existing_owner"
    assert result["matched_speaker_user_id"] == "scott"


def test_foreign_absorption_allowed_when_foreign_is_clearly_better() -> None:
    from voice_agent.auth.voiceprint_graph import VoiceprintGraphStore

    session = _FakeSession(
        speaker_match_row={"speaker_user_id": "intruder", "score": 0.97},
        owner_match_row={"score": 0.60},
        global_speaker_row=None,
    )
    store = _make_store_with_session(session)
    result = store.link_identity_to_global_speakers("scott", [0.1, 0.2, 0.3], match_threshold=0.80)
    assert result["method"] == "embedding_match"
    assert result["matched_speaker_user_id"] == "intruder"


def test_device_scope_save_triggers_speaker_linkage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from voice_agent.auth.voiceprint_graph import VoiceprintGraphStore

    store = VoiceprintGraphStore.__new__(VoiceprintGraphStore)
    store.EMBEDDING_DIMENSION = 3
    store.database = "neo4j"

    calls: dict[str, object] = {}

    def fake_get_active_record(user_id: str):
        calls["get_active_record"] = user_id
        return {"embedding": [0.1, 0.2, 0.3]}

    def fake_link(user_id, embedding, *, match_threshold=0.85):
        calls["link_user_id"] = user_id
        calls["link_embedding"] = list(embedding)
        return {"linked": True, "method": "label_match"}

    monkeypatch.setattr(store, "get_active_record", fake_get_active_record)
    monkeypatch.setattr(store, "link_identity_to_global_speakers", fake_link)

    # Avoid touching Neo4j for the version/group writes.
    fake_session = _FakeSession()
    store._driver = lambda: _FakeDriver(fake_session)  # type: ignore[assignment]

    store.save_voiceprint(
        user_id="scott",
        embedding_mean=[0.1, 0.2, 0.3],
        samples=_sample_samples(),
        threshold=0.75,
        source="seed",
        append=False,
        device_id="phone",
    )
    # Device-scope save must still keep the owner's global Speaker current,
    # using the identity-scope embedding.
    assert calls.get("link_user_id") == "scott"
    assert calls.get("link_embedding") == [0.1, 0.2, 0.3]
