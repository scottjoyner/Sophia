from __future__ import annotations

import os
from pathlib import Path

import pytest

from voice_agent.auth.registry import _jsonable
from voice_agent.auth.verify import _compute_adaptive_threshold
from voice_agent.auth.voiceprint_graph import _neo4j_json
from voice_agent.config import AppConfig, PathsConfig


def _auth_config(**overrides):
    config = AppConfig(paths=PathsConfig(artifacts_dir="/tmp/opencode-nonuse", workspace_dir="/tmp/opencode-nonuse"))
    if overrides:
        config.auth = config.auth.model_copy(update=overrides)
    return config


def test_adaptive_threshold_disabled_returns_base():
    config = _auth_config(adaptive_threshold_enabled=False)
    calib = {"accepted_mean": 0.95, "rejected_mean": 0.5, "n_accepted": 5, "n_rejected": 2}
    assert _compute_adaptive_threshold(0.75, calib, config) == 0.75


def test_adaptive_threshold_no_calibration_returns_base():
    config = _auth_config(adaptive_threshold_enabled=True)
    assert _compute_adaptive_threshold(0.75, None, config) == 0.75


def test_adaptive_threshold_lowers_for_easy_device():
    config = _auth_config(
        adaptive_threshold_enabled=True,
        adaptive_threshold_min=0.6,
        adaptive_threshold_max=1.0,
        adaptive_threshold_margin=0.05,
    )
    calib = {"accepted_mean": 0.95, "rejected_mean": 0.5, "n_accepted": 5, "n_rejected": 2}
    assert _compute_adaptive_threshold(0.75, calib, config) == pytest.approx(0.90)


def test_adaptive_threshold_floor_and_rejected_guard():
    config = _auth_config(
        adaptive_threshold_enabled=True,
        adaptive_threshold_min=0.6,
        adaptive_threshold_max=1.0,
        adaptive_threshold_margin=0.05,
    )
    calib = {"accepted_mean": 0.62, "rejected_mean": 0.55, "n_accepted": 5, "n_rejected": 2}
    # candidate = 0.62 - 0.05 = 0.57, guarded by rejected+margin = 0.60, clamped to min 0.60
    assert _compute_adaptive_threshold(0.75, calib, config) == pytest.approx(0.60)


def test_device_calibration_persists_and_averages(tmp_path: Path):
    from voice_agent.auth.registry import VoiceprintRegistry

    db = tmp_path / "vp.sqlite"
    config = _auth_config(adaptive_threshold_enabled=True, adaptive_threshold_alpha=0.5)
    registry = VoiceprintRegistry(db, config)
    registry.record_device_outcome("phone", 0.9, True, alpha=0.5)
    registry.record_device_outcome("phone", 0.8, True, alpha=0.5)
    calib = registry.fetch_device_calibration("phone")
    assert calib is not None
    assert abs(calib["accepted_mean"] - 0.85) < 1e-9
    assert calib["n_accepted"] == 2
    registry.record_device_outcome("phone", 0.4, False, alpha=0.5)
    calib = registry.fetch_device_calibration("phone")
    assert calib["n_rejected"] == 1
    assert abs(calib["rejected_mean"] - 0.4) < 1e-9


@pytest.mark.skipif(not os.getenv("NEO4J_PASSWORD"), reason="Neo4j not configured in this environment")
def test_global_speaker_linking_live(tmp_path: Path):
    import numpy as np

    from voice_agent.auth.voiceprint_graph import VoiceprintGraphStore

    config = AppConfig(
        paths=PathsConfig(artifacts_dir=str(tmp_path / "runs"), workspace_dir=str(tmp_path / "workspace")),
    )
    config.neo4j = config.neo4j.model_copy(
        update={
            "uri": os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            "user": os.getenv("NEO4J_USER", "neo4j"),
            "password": os.getenv("NEO4J_PASSWORD"),
            "database": os.getenv("NEO4J_DATABASE", "neo4j"),
        }
    )
    store = VoiceprintGraphStore.from_config(config.neo4j)
    assert store is not None
    user = "scott_adaptive_test"
    emb = (np.random.rand(VoiceprintGraphStore.EMBEDDING_DIMENSION) - 0.5).tolist()
    res = store.link_identity_to_global_speakers(user, emb, match_threshold=0.85)
    assert res["linked"] is True
    assert store.get_identity_linkage(user)
    backfill = store.backfill_global_speaker_embeddings()
    assert backfill["errors"] == 0


class _FakeDateTime:
    """Duck-typed stand-in for neo4j.time.DateTime used by the stores."""

    def isoformat(self) -> str:
        return "2026-07-14T00:00:00+00:00"


def test_neo4j_json_makes_temporal_values_serializable():
    assert _neo4j_json(None) is None
    assert _neo4j_json("x") == "x"
    assert _neo4j_json(1.5) == 1.5
    assert _neo4j_json(_FakeDateTime()) == "2026-07-14T00:00:00+00:00"


def test_registry_jsonable_converts_temporal_values():
    assert _jsonable(_FakeDateTime()) == "2026-07-14T00:00:00+00:00"
    assert _jsonable("plain") == "plain"
    assert _jsonable(3) == 3
