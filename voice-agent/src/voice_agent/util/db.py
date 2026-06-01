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
