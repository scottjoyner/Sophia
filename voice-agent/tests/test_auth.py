from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from voice_agent.auth.enroll import EnrollmentError, enroll_from_files
from voice_agent.auth.registry import VoiceprintRegistry
from voice_agent.auth.verify import (
    _build_candidate,
    _compute_adaptive_threshold,
    _effective_threshold,
    cosine_similarity,
)
from voice_agent.config import AppConfig


def _make_wav(path: Path, seconds: float = 3.0, sr: int = 16000, freq: float = 220.0) -> Path:
    from voice_agent.util.audio import write_wav

    n = int(seconds * sr)
    samples = (0.4 * np.sin(2 * np.pi * freq * np.arange(n) / sr)).astype(np.float32)
    write_wav(path, samples, sr)
    return path


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        paths={
            "artifacts_dir": str(tmp_path / "runs"),
            "workspace_dir": str(tmp_path / "ws"),
            "capture_dir": str(tmp_path / "cap"),
        }
    )


def test_cosine_similarity() -> None:
    a = np.array([1.0, 0.0, 0.0])
    b = np.array([1.0, 0.0, 0.0])
    assert cosine_similarity(a, b) == pytest.approx(1.0)
    assert cosine_similarity(np.zeros(3), b) == 0.0


def test_compute_adaptive_threshold_branches() -> None:
    cfg = _config(Path("/tmp"))
    cfg.auth.adaptive_threshold_enabled = False
    assert _compute_adaptive_threshold(0.7, None, cfg) == 0.7
    cfg.auth.adaptive_threshold_enabled = True
    assert _compute_adaptive_threshold(0.7, None, cfg) == 0.7
    cal = {"accepted_mean": 0.95, "rejected_mean": 0.2}
    assert _compute_adaptive_threshold(0.7, cal, cfg) == pytest.approx(0.95 - cfg.auth.adaptive_threshold_margin)
    cfg.auth.adaptive_threshold_min = 0.99
    assert _compute_adaptive_threshold(0.7, cal, cfg) == pytest.approx(0.99)


def test_effective_threshold_device_calibration() -> None:
    cfg = _config(Path("/tmp"))
    record = {"device_id": "phone", "threshold": 0.75}
    assert _effective_threshold(record, 0.75, cfg, None) == 0.75
    record_no_dev = {"device_id": None, "threshold": 0.75}
    assert _effective_threshold(record_no_dev, 0.75, cfg, None) == 0.75


def test_build_candidate_marks_acceptance() -> None:
    record = {"version_id": "v1", "device_id": "d1", "candidate_id": "c1"}
    accepted = _build_candidate(record, 0.9, 0.75)
    assert accepted["accepted"] is True
    rejected = _build_candidate(record, 0.5, 0.75)
    assert rejected["accepted"] is False


def test_validate_clip_too_short_and_silent(tmp_path) -> None:
    from voice_agent.auth.enroll import _validate_clip

    short = _make_wav(tmp_path / "short.wav", seconds=0.005)
    with pytest.raises(EnrollmentError):
        _validate_clip(str(short), np.zeros(80, dtype=np.float32), 16000, 2.0, 30.0)
    silent = _make_wav(tmp_path / "silent.wav", seconds=3.0)
    n = 16000 * 3
    with wave.open(str(silent), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(np.zeros(n, dtype=np.int16).tobytes())
    with pytest.raises(EnrollmentError):
        _validate_clip(str(silent), np.zeros(n, dtype=np.float32), 16000, 2.0, 30.0)


def test_enroll_from_files_and_verify_flow(tmp_path) -> None:
    cfg = _config(tmp_path)
    wav = _make_wav(tmp_path / "voice.wav", seconds=3.0)
    result = enroll_from_files(cfg, "alice", [str(wav)], min_seconds=0.01, max_seconds=30.0)
    assert result["sample_count"] == 1
    assert result["appended"] is False
    assert result["graph_enabled"] is False

    from voice_agent.auth.verify import verify_audio_segment
    from voice_agent.util.audio import read_wav

    samples, sr = read_wav(str(wav))
    out = verify_audio_segment(cfg, "sess", "alice", samples, sr)
    assert out["accepted"] is True
    assert out["user_id"] == "alice"

    unknown = verify_audio_segment(cfg, "sess", "bob", samples, sr)
    assert unknown["accepted"] is False


def test_enroll_skips_duplicate_sha(tmp_path) -> None:
    cfg = _config(tmp_path)
    wav = _make_wav(tmp_path / "voice.wav", seconds=3.0)
    first = enroll_from_files(cfg, "alice", [str(wav)], min_seconds=0.01, max_seconds=30.0, append=True)
    second = enroll_from_files(cfg, "alice", [str(wav)], min_seconds=0.01, max_seconds=30.0, append=True)
    assert second["sample_count"] == first["sample_count"]


def test_enroll_no_usable_clips_raises(tmp_path) -> None:
    cfg = _config(tmp_path)
    silent = _make_wav(tmp_path / "silent.wav", seconds=3.0)
    n = 16000 * 3
    with wave.open(str(silent), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(np.zeros(n, dtype=np.int16).tobytes())
    with pytest.raises(EnrollmentError):
        enroll_from_files(cfg, "alice", [str(silent)], min_seconds=0.01, max_seconds=30.0)


class _FakeGraph:
    def __init__(self):
        self.saved = []
        self.deleted = []

    def save_voiceprint(self, **kwargs):
        self.saved.append(kwargs)
        return {
            "user_id": kwargs["user_id"],
            "group_key": f"{kwargs['user_id']}:identity",
            "scope": "identity",
            "device_id": kwargs.get("device_id"),
            "version_id": "v1",
            "embedding": list(kwargs["embedding_mean"]),
            "samples": kwargs["samples"],
            "threshold": kwargs["threshold"],
            "sample_count": 1,
            "source": kwargs["source"],
            "append": kwargs["append"],
            "lineage_mode": "initial",
            "active": True,
            "captured_in_graph": True,
        }

    def get_active_record(self, user_id):
        return {
            "user_id": user_id,
            "device_id": None,
            "version_id": "v1",
            "group_key": f"{user_id}:identity",
            "scope": "identity",
            "embedding": [0.1, 0.2, 0.3],
            "samples": {"samples": []},
            "threshold": 0.75,
            "sample_count": 1,
            "source": "manual",
            "append": False,
            "lineage_mode": "initial",
            "active": True,
            "created_at": "2026-01-01",
        }

    def get_active_records(self, user_id):
        identity = self.get_active_record(user_id)
        device = dict(identity)
        device["device_id"] = "phone"
        device["scope"] = "device"
        device["group_key"] = f"{user_id}:device:phone"
        return [identity, device]

    def get_device_record(self, user_id, device_id):
        return {
            "user_id": user_id,
            "device_id": device_id,
            "version_id": "vd1",
            "group_key": f"{user_id}:device:{device_id}",
            "scope": "device",
            "embedding": [0.4, 0.5, 0.6],
            "samples": {"samples": []},
            "threshold": 0.7,
            "sample_count": 1,
            "source": "manual",
            "append": False,
            "lineage_mode": "initial",
            "active": True,
            "created_at": "2026-01-01",
        }

    def get_historical_candidates(self, user_id):
        return [
            {
                "user_id": user_id,
                "device_id": None,
                "version_id": "v1",
                "group_key": f"{user_id}:identity",
                "scope": "identity",
                "embedding": [0.1, 0.2, 0.3],
                "samples_json": "{\"samples\": []}",
                "threshold": 0.75,
                "sample_count": 1,
                "source": "manual",
                "append": False,
                "lineage_mode": "initial",
                "active": True,
                "created_at": "2026-01-01",
            }
        ]

    def list_user_ids(self):
        return ["alice", "bob"]

    def delete_device_voiceprint(self, user_id, device_id):
        self.deleted.append((user_id, device_id))
        return True

    def backfill_global_speaker_embeddings(self, match_threshold=None):
        return {"ok": True, "linked": 1}


def test_registry_sqlite_roundtrip(tmp_path) -> None:
    cfg = _config(tmp_path)
    reg = VoiceprintRegistry(tmp_path / "r.sqlite", cfg)
    reg.save("alice", [0.1, 0.2], {"samples": [{"sha256": "x", "embedding": [0.1, 0.2]}]}, 0.8)
    rec = reg.get("alice")
    assert rec is not None
    assert rec["embedding"] == [0.1, 0.2]
    assert reg.list_users() == ["alice"]
    assert reg.sample_count("alice") == 1
    assert reg.get_historical_candidates("alice") == []
    assert reg.backfill_global_speaker_embeddings() == {"ok": False, "error": "Neo4j not configured"}
    assert reg.reconcile_to_neo4j() == {"ok": False, "error": "Neo4j not configured"}


def test_registry_graph_routing(tmp_path) -> None:
    cfg = _config(tmp_path)
    reg = VoiceprintRegistry(tmp_path / "r.sqlite", cfg)
    reg.graph = _FakeGraph()
    res = reg.save("alice", [0.1, 0.2], {"samples": []}, 0.8)
    assert res["graph_saved"] is True
    assert reg.get("alice")["user_id"] == "alice"
    assert reg.get_best("alice")["device_id"] == "default"
    assert set(reg.list_users()) == {"alice", "bob"}
    assert reg.delete_device("alice", "phone") is True
    assert reg.backfill_global_speaker_embeddings(0.85)["ok"] is True


def test_registry_graph_device_and_historical_routing(tmp_path) -> None:
    cfg = _config(tmp_path)
    reg = VoiceprintRegistry(tmp_path / "r.sqlite", cfg)
    reg.graph = _FakeGraph()
    reg.save_device("alice", "phone", [0.4, 0.5], {"samples": []}, 0.7)
    device = reg.get_device("alice", "phone")
    assert device is not None
    assert device["device_id"] == "phone"
    devices = reg.get_devices("alice")
    assert "phone" in devices
    assert reg.list_devices("alice") == ["phone"]
    all_records = reg.get_all_for_user("alice")
    assert any(r.get("device_id") == "default" for r in all_records)
    candidates = reg.get_historical_candidates("alice")
    assert candidates and candidates[0]["sample_count"] == 1
    best = reg.get_best("alice")
    assert best is not None


def test_challenge_random_phrase() -> None:
    from voice_agent.auth.challenge import random_phrase

    phrase = random_phrase(None)
    assert isinstance(phrase, str)
    assert len(phrase) > 0
