from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict


class CaptureIdempotencyStore:
    """SQLite cache for browser retry idempotency.

    This is operational state, not memory. It lets the server return the same
    capture response when an offline browser queue retries the same
    `client_capture_id` after an uncertain network outcome.
    """

    def __init__(self, db_path: Path, ttl_ms: int = 30 * 24 * 60 * 60 * 1000) -> None:
        self.db_path = Path(db_path)
        self.ttl_ms = ttl_ms
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @staticmethod
    def now_ms() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def normalize_key(client_capture_id: str | None) -> str:
        return (client_capture_id or "").strip()[:128]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS capture_idempotency (
                  client_capture_id TEXT PRIMARY KEY,
                  capture_id TEXT NOT NULL,
                  response_json TEXT NOT NULL,
                  created_at_ms INTEGER NOT NULL,
                  updated_at_ms INTEGER NOT NULL,
                  expires_at_ms INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_capture_idempotency_expiry
                ON capture_idempotency(expires_at_ms)
                """
            )

    def get(self, client_capture_id: str | None) -> Dict[str, Any] | None:
        key = self.normalize_key(client_capture_id)
        if not key:
            return None
        now = self.now_ms()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM capture_idempotency WHERE client_capture_id = ?",
                (key,),
            ).fetchone()
            if not row:
                return None
            if int(row["expires_at_ms"]) <= now:
                conn.execute("DELETE FROM capture_idempotency WHERE client_capture_id = ?", (key,))
                return None
            conn.execute(
                "UPDATE capture_idempotency SET updated_at_ms = ? WHERE client_capture_id = ?",
                (now, key),
            )
        payload = json.loads(row["response_json"])
        payload["idempotent_replay"] = True
        payload["client_capture_id"] = key
        return payload

    def put(self, client_capture_id: str | None, capture_id: str, response: Dict[str, Any]) -> None:
        key = self.normalize_key(client_capture_id)
        if not key:
            return
        now = self.now_ms()
        expires = now + self.ttl_ms
        clean_response = dict(response)
        clean_response["client_capture_id"] = key
        clean_response["idempotent_replay"] = False
        response_json = json.dumps(clean_response, ensure_ascii=False, sort_keys=True, default=str)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO capture_idempotency (
                  client_capture_id, capture_id, response_json, created_at_ms, updated_at_ms, expires_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(client_capture_id) DO UPDATE SET
                  response_json = excluded.response_json,
                  updated_at_ms = excluded.updated_at_ms,
                  expires_at_ms = excluded.expires_at_ms
                """,
                (key, capture_id, response_json, now, now, expires),
            )

    def counts(self) -> Dict[str, int]:
        now = self.now_ms()
        with self._connect() as conn:
            row = conn.execute("SELECT count(*) AS n FROM capture_idempotency").fetchone()
            active = conn.execute(
                "SELECT count(*) AS n FROM capture_idempotency WHERE expires_at_ms > ?",
                (now,),
            ).fetchone()
            expired = conn.execute(
                "SELECT count(*) AS n FROM capture_idempotency WHERE expires_at_ms <= ?",
                (now,),
            ).fetchone()
        return {
            "total": int(row["n"] if row else 0),
            "active": int(active["n"] if active else 0),
            "expired": int(expired["n"] if expired else 0),
        }

    def summary(self) -> Dict[str, Any]:
        counts = self.counts()
        return {
            "counts": counts,
            "healthy": counts["expired"] == 0,
            "ttl_ms": self.ttl_ms,
        }

    def prune_expired(self) -> int:
        now = self.now_ms()
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM capture_idempotency WHERE expires_at_ms <= ?", (now,))
            return cur.rowcount
