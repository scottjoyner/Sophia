from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # The app is exercised from multiple threads in tests and under ASGI.
        # Keep one connection and guard it with a lock instead of relying on
        # SQLite's default same-thread restriction.
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._init()

    def _init(self) -> None:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_ms INTEGER,
                    session_id TEXT,
                    type TEXT,
                    payload TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS voiceprints (
                    user_id TEXT PRIMARY KEY,
                    embedding_mean TEXT,
                    samples_json TEXT,
                    threshold REAL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS voiceprint_devices (
                    user_id TEXT,
                    device_id TEXT,
                    embedding_mean TEXT,
                    samples_json TEXT,
                    threshold REAL,
                    PRIMARY KEY (user_id, device_id)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS device_calibration (
                    device_id TEXT PRIMARY KEY,
                    accepted_mean REAL,
                    rejected_mean REAL,
                    n_accepted INTEGER,
                    n_rejected INTEGER,
                    updated_at TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS task_outbox (
                    task_outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_ts_ms INTEGER,
                    user_id TEXT,
                    device_id TEXT,
                    session_id TEXT,
                    event_id TEXT,
                    correlation_id TEXT,
                    task_title TEXT,
                    task_json TEXT,
                    payload_json TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    last_response_json TEXT,
                    dispatch_id TEXT,
                    task_id TEXT,
                    updated_at TEXT
                )
                """
            )
            self.conn.commit()

    def log_event(self, session_id: str, event_type: str, payload: dict[str, Any]) -> None:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "INSERT INTO events (ts_ms, session_id, type, payload) VALUES (?, ?, ?, ?)",
                (payload.get("ts_ms"), session_id, event_type, json.dumps(payload)),
            )
            self.conn.commit()

    def save_voiceprint(
        self, user_id: str, embedding_mean: Iterable[float], samples: dict[str, Any], threshold: float
    ) -> None:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                """
                INSERT INTO voiceprints (user_id, embedding_mean, samples_json, threshold)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    embedding_mean=excluded.embedding_mean,
                    samples_json=excluded.samples_json,
                    threshold=excluded.threshold
                """,
                (user_id, json.dumps(list(embedding_mean)), json.dumps(samples), threshold),
            )
            self.conn.commit()

    def fetch_voiceprint(self, user_id: str) -> dict[str, Any] | None:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("SELECT embedding_mean, samples_json, threshold FROM voiceprints WHERE user_id=?", (user_id,))
            row = cur.fetchone()
        if not row:
            return None
        embedding = json.loads(row[0])
        samples = json.loads(row[1])
        threshold = row[2]
        return {"embedding": embedding, "samples": samples, "threshold": threshold}

    def save_device_voiceprint(
        self, user_id: str, device_id: str, embedding_mean: Iterable[float], samples: dict[str, Any], threshold: float
    ) -> None:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                """
                INSERT INTO voiceprint_devices (user_id, device_id, embedding_mean, samples_json, threshold)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, device_id) DO UPDATE SET
                    embedding_mean=excluded.embedding_mean,
                    samples_json=excluded.samples_json,
                    threshold=excluded.threshold
                """,
                (user_id, device_id, json.dumps(list(embedding_mean)), json.dumps(samples), threshold),
            )
            self.conn.commit()

    def fetch_device_voiceprints(self, user_id: str) -> dict[str, dict[str, Any]]:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT device_id, embedding_mean, samples_json, threshold FROM voiceprint_devices WHERE user_id=?",
                (user_id,),
            )
            devices = {}
            for row in cur.fetchall():
                device_id, embedding_json, samples_json, threshold = row
                devices[device_id] = {
                    "embedding": json.loads(embedding_json),
                    "samples": json.loads(samples_json),
                    "threshold": threshold,
                }
        return devices

    def list_device_ids(self, user_id: str) -> list[str]:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("SELECT device_id FROM voiceprint_devices WHERE user_id=?", (user_id,))
            return [row[0] for row in cur.fetchall()]

    def record_device_outcome(self, device_id: str, score: float, accepted: bool, alpha: float = 0.1) -> None:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT accepted_mean, rejected_mean, n_accepted, n_rejected FROM device_calibration WHERE device_id=?",
                (device_id,),
            )
            row = cur.fetchone()
            acc = row[0] if row else None
            rej = row[1] if row else None
            n_acc = (row[2] or 0) if row else 0
            n_rej = (row[3] or 0) if row else 0
            if accepted:
                acc = score if acc is None else alpha * score + (1 - alpha) * acc
                n_acc += 1
            else:
                rej = score if rej is None else alpha * score + (1 - alpha) * rej
                n_rej += 1
            cur.execute(
                """
                INSERT INTO device_calibration (device_id, accepted_mean, rejected_mean, n_accepted, n_rejected, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(device_id) DO UPDATE SET
                    accepted_mean=excluded.accepted_mean,
                    rejected_mean=excluded.rejected_mean,
                    n_accepted=excluded.n_accepted,
                    n_rejected=excluded.n_rejected,
                    updated_at=excluded.updated_at
                """,
                (device_id, acc, rej, n_acc, n_rej),
            )
            self.conn.commit()

    def fetch_device_calibration(self, device_id: str) -> dict[str, Any] | None:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT accepted_mean, rejected_mean, n_accepted, n_rejected FROM device_calibration WHERE device_id=?",
                (device_id,),
            )
            row = cur.fetchone()
        if not row or (row[0] is None and row[1] is None):
            return None
        return {
            "device_id": device_id,
            "accepted_mean": row[0],
            "rejected_mean": row[1],
            "n_accepted": row[2] or 0,
            "n_rejected": row[3] or 0,
        }

    def enqueue_task(
        self,
        *,
        user_id: str,
        device_id: str | None,
        session_id: str,
        event_id: str | None,
        correlation_id: str | None,
        task_title: str,
        task_json: dict[str, Any],
        payload_json: dict[str, Any],
    ) -> int:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                """
                INSERT INTO task_outbox (
                    created_ts_ms, user_id, device_id, session_id, event_id, correlation_id,
                    task_title, task_json, payload_json, status, attempts, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, datetime('now'))
                """,
                (
                    payload_json.get("ts_ms"),
                    user_id,
                    device_id,
                    session_id,
                    event_id,
                    correlation_id,
                    task_title,
                    json.dumps(task_json, ensure_ascii=False),
                    json.dumps(payload_json, ensure_ascii=False),
                ),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def mark_task_dispatched(
        self,
        task_outbox_id: int,
        *,
        sent: bool,
        dispatch_id: str | None = None,
        task_id: str | None = None,
        response: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        status = "sent" if sent else "failed"
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                """
                UPDATE task_outbox
                SET status = ?, attempts = attempts + 1, last_error = ?, last_response_json = ?,
                    dispatch_id = ?, task_id = ?, updated_at = datetime('now')
                WHERE task_outbox_id = ?
                """,
                (
                    status,
                    error,
                    json.dumps(response, ensure_ascii=False) if response is not None else None,
                    dispatch_id,
                    task_id,
                    task_outbox_id,
                ),
            )
            self.conn.commit()

    def requeue_failed_task(self, task_outbox_id: int) -> None:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "UPDATE task_outbox SET status = 'pending', updated_at = datetime('now') WHERE task_outbox_id = ?",
                (task_outbox_id,),
            )
            self.conn.commit()

    def list_tasks(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            cur = self.conn.cursor()
            if status:
                cur.execute(
                    "SELECT * FROM task_outbox WHERE status = ? ORDER BY task_outbox_id DESC LIMIT ?",
                    (status, limit),
                )
            else:
                cur.execute("SELECT * FROM task_outbox ORDER BY task_outbox_id DESC LIMIT ?", (limit,))
            rows = cur.fetchall()
        out = []
        for row in rows:
            rec = dict(row)
            for field in ("task_json", "payload_json", "last_response_json"):
                if rec.get(field):
                    try:
                        rec[field] = json.loads(rec[field])
                    except Exception:
                        pass
            out.append(rec)
        return out

    def task_summary(self) -> dict[str, int]:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("SELECT status, COUNT(*) FROM task_outbox GROUP BY status")
            rows = cur.fetchall()
        summary = {status: 0 for status in ("pending", "sent", "failed")}
        for row in rows:
            summary[row[0]] = row[1]
        return summary
