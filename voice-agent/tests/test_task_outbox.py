from __future__ import annotations

import time
from pathlib import Path

import pytest

from voice_agent.server.assistant import (
    Assistant,
    reconcile_tasks,
    retry_failed_tasks,
)
from voice_agent.util.db import Database


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "results.sqlite")


def test_task_outbox_round_trip(db: Database) -> None:
    outbox_id = db.enqueue_task(
        user_id="scott",
        device_id=None,
        session_id="s1",
        event_id="evt-1",
        correlation_id="corr-1",
        task_title="Buy milk",
        task_json={"title": "Buy milk"},
        payload_json={"event_id": "evt-1", "ts_ms": 1},
    )
    assert outbox_id > 0
    db.mark_task_dispatched(outbox_id, sent=False, error="boom")
    summary = db.task_summary()
    assert summary["pending"] == 0
    assert summary["failed"] == 1
    rows = db.list_tasks(status="failed")
    assert rows[0]["task_outbox_id"] == outbox_id
    assert rows[0]["last_error"] == "boom"
    assert rows[0]["attempts"] == 1

    db.requeue_failed_task(outbox_id)
    assert db.task_summary()["pending"] == 1
    assert db.task_summary()["failed"] == 0


def test_ingest_tasks_persists_to_outbox(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    # No AssistX secret configured -> dispatch returns sent=False but still records.
    monkeypatch.delenv("ASSISTX_VOICE_WEBHOOK_SECRET", raising=False)
    assistant = Assistant.__new__(Assistant)
    assistant.config = None  # type: ignore[assignment]
    assistant.db = db

    results = assistant.ingest_tasks(
        [{"title": "Email Alice", "description": "Follow up", "priority": "high"}],
        session_id="s2",
        actor={"user_id": "scott", "device_id": None},
    )
    assert len(results) == 1
    assert results[0]["outbox_id"] is not None
    summary = db.task_summary()
    assert summary["failed"] == 1  # dispatch failed (no secret)
    assert summary["sent"] == 0
    row = db.list_tasks(status="failed")[0]
    assert row["task_title"] == "Email Alice"
    assert row["payload_json"]["event_id"] == results[0]["event_id"]


def test_reconcile_tasks_reports_drift_and_requeues(db: Database) -> None:
    db.enqueue_task(
        user_id="scott",
        device_id=None,
        session_id="s3",
        event_id="evt-2",
        correlation_id=None,
        task_title="Task A",
        task_json={"title": "Task A"},
        payload_json={"event_id": "evt-2", "ts_ms": int(time.time() * 1000)},
    )
    report = reconcile_tasks(db, requeue_failed=False)
    assert report["summary"]["pending"] == 1
    assert report["pending_count"] == 1
    assert report["check_only"] is True
    assert report["dead_letter_count"] == 0


def test_retry_failed_tasks_marks_sent(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_dispatch(payload, **kwargs):
        return {
            "event_id": payload.get("event_id"),
            "sent": True,
            "error": None,
            "response": {"task_id": "t-1"},
            "correlation_id": None,
            "task_id": "t-1",
            "dispatch_id": "d-1",
        }

    monkeypatch.setattr("voice_agent.server.assistant.dispatch_to_assistx", fake_dispatch)

    outbox_id = db.enqueue_task(
        user_id="scott",
        device_id=None,
        session_id="s4",
        event_id="evt-3",
        correlation_id=None,
        task_title="Task B",
        task_json={"title": "Task B"},
        payload_json={"event_id": "evt-3", "ts_ms": int(time.time() * 1000)},
    )
    db.mark_task_dispatched(outbox_id, sent=False, error="prev fail")

    result = retry_failed_tasks(db)
    assert result["attempted"] == 1
    assert result["succeeded"] == 1
    assert db.task_summary()["sent"] == 1


def test_tasks_reconcile_endpoints_require_session(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from voice_agent.config import AppConfig, PathsConfig
    from voice_agent.server.app import create_app

    config = AppConfig(paths=PathsConfig(artifacts_dir=str(tmp_path / "runs"), workspace_dir=str(tmp_path / "workspace")))
    client = TestClient(create_app(config))

    # Unauthenticated -> 401
    assert client.get("/tasks/reconcile").status_code in (401, 403)
    assert client.post("/tasks/reconcile").status_code in (401, 403)

    client.post("/auth/login", json={"passphrase": "sophia"})
    status = client.get("/tasks/reconcile").json()
    assert "summary" in status
    assert status["check_only"] is True
    retry = client.post("/tasks/reconcile").json()
    assert "retry" in retry
