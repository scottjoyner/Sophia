from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self._init()

    def _init(self) -> None:
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
        self.conn.commit()

    def log_event(self, session_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO events (ts_ms, session_id, type, payload) VALUES (?, ?, ?, ?)",
            (payload.get("ts_ms"), session_id, event_type, json.dumps(payload)),
        )
        self.conn.commit()

    def save_voiceprint(
        self, user_id: str, embedding_mean: Iterable[float], samples: Dict[str, Any], threshold: float
    ) -> None:
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
        cur = self.conn.cursor()
        cur.execute("SELECT embedding_mean, samples_json, threshold FROM voiceprints WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if not row:
            return None
        embedding = json.loads(row[0])
        samples = json.loads(row[1])
        threshold = row[2]
        return {"embedding": embedding, "samples": samples, "threshold": threshold}
