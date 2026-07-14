from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, Iterable


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
            self.conn.commit()

    def log_event(self, session_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "INSERT INTO events (ts_ms, session_id, type, payload) VALUES (?, ?, ?, ?)",
                (payload.get("ts_ms"), session_id, event_type, json.dumps(payload)),
            )
            self.conn.commit()

    def save_voiceprint(
        self, user_id: str, embedding_mean: Iterable[float], samples: Dict[str, Any], threshold: float
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

    def fetch_voiceprint(self, user_id: str) -> Dict[str, Any] | None:
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
        self, user_id: str, device_id: str, embedding_mean: Iterable[float], samples: Dict[str, Any], threshold: float
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

    def fetch_device_voiceprints(self, user_id: str) -> Dict[str, Dict[str, Any]]:
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

    def fetch_device_calibration(self, device_id: str) -> Dict[str, Any] | None:
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
