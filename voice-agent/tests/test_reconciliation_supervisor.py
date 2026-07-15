from __future__ import annotations

import time
from pathlib import Path

import pytest

from voice_agent.server.graph_outbox import GraphOutbox
from voice_agent.server.reconciliation_supervisor import ReconciliationSupervisor
from voice_agent.util.db import Database


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "results.sqlite")


def test_supervisor_sweeps_all_workers(db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, int] = {}

    def fake_retry(d):
        calls["tasks"] = calls.get("tasks", 0) + 1
        return {"attempted": 0, "succeeded": 0, "failed": 0}

    def fake_reconcile(self, source="reconcile", force=False, check_only=False):
        calls["voiceprints"] = calls.get("voiceprints", 0) + 1
        return {"drift": 0, "graph_only": 0}

    monkeypatch.setattr("voice_agent.server.assistant.retry_failed_tasks", fake_retry)

    outbox = GraphOutbox(tmp_path / "results.sqlite")
    sup = ReconciliationSupervisor.__new__(ReconciliationSupervisor)
    sup.config = _fake_config()
    sup.task_db = db
    sup.graph_outbox = outbox
    class FakeRegistry:
        def reconcile_to_neo4j(self, source="reconcile", force=False, check_only=False):
            return fake_reconcile(self, source, force, check_only)

    sup.registry = FakeRegistry()
    sup._task = None
    sup._stop = False
    sup.last_runs = {}

    import asyncio

    result = asyncio.run(sup.run_once())
    assert calls.get("tasks") == 1
    assert calls.get("voiceprints") == 1
    assert "graph_outbox" in result


def test_save_capture_enqueues_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from voice_agent.auth import neo4j_ingest

    outbox = GraphOutbox(tmp_path / "results.sqlite")

    import neo4j

    def boom(*a, **k):
        raise RuntimeError("neo4j down")

    monkeypatch.setattr(neo4j.GraphDatabase, "driver", staticmethod(boom))

    with pytest.raises(RuntimeError):
        neo4j_ingest.save_capture_to_neo4j(
            "bolt://x",
            "neo4j",
            "pw",
            user_id="scott",
            capture_id="cap-1",
            transcript="hi",
            audio_path="/a.wav",
            content_type="audio/wav",
            outbox=outbox,
        )
    items = outbox.list_due(limit=10)
    assert len(items) == 1
    assert items[0].kind == "capture"
    assert items[0].idempotency_key == "server:cap-1"


def test_graph_outbox_replay_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from voice_agent.auth import neo4j_ingest
    from voice_agent.server.graph_outbox import replay_graph_outbox_items

    outbox = GraphOutbox(tmp_path / "results.sqlite")
    outbox.enqueue(
        kind="capture",
        idempotency_key="server:cap-2",
        payload={
            "user_id": "scott",
            "capture_id": "cap-2",
            "transcript": "hi",
            "audio_path": "/a.wav",
            "content_type": "audio/wav",
            "metadata": {},
            "context": {},
        },
    )

    recorded = {}

    def fake_save(uri, user, pw, *, user_id, capture_id, transcript, audio_path, content_type, database=None, **kwargs):
        recorded["capture_id"] = capture_id

    monkeypatch.setattr(neo4j_ingest, "save_capture_to_neo4j", fake_save)

    result = replay_graph_outbox_items(
        outbox, neo4j_uri="bolt://x", neo4j_user="neo4j", neo4j_password="pw", limit=10
    )
    assert result["succeeded"] == 1
    assert result["failed"] == 0
    assert recorded["capture_id"] == "cap-2"
    assert outbox.summary()["healthy"] is True


def test_supervisor_status_aggregates(db: Path, tmp_path: Path) -> None:
    outbox = GraphOutbox(tmp_path / "results.sqlite")
    sup = ReconciliationSupervisor.__new__(ReconciliationSupervisor)
    sup.config = _fake_config()
    sup.task_db = db
    sup.graph_outbox = outbox
    sup.registry = None
    sup._task = None
    sup._stop = False
    sup.last_runs = {}

    status = sup.status()
    assert "components" in status
    assert "graph_outbox" in status["components"]
    assert "tasks" in status["components"]
    assert status["enabled"] is True


def test_system_reconcile_endpoints_require_session(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from voice_agent.config import AppConfig, PathsConfig
    from voice_agent.server.app import create_app

    config = AppConfig(paths=PathsConfig(artifacts_dir=str(tmp_path / "runs"), workspace_dir=str(tmp_path / "workspace")))
    client = TestClient(create_app(config))

    assert client.get("/system/reconcile/status").status_code in (401, 403)
    assert client.post("/system/reconcile").status_code in (401, 403)

    client.post("/auth/login", json={"passphrase": "sophia"})
    status = client.get("/system/reconcile/status").json()
    assert "components" in status
    triggered = client.post("/system/reconcile").json()
    assert "status" in triggered


def _fake_config():
    from voice_agent.config import AppConfig

    cfg = AppConfig()
    cfg.reconciliation.enabled = True
    return cfg
