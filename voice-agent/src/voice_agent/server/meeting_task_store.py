from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from ..util.db import Database

_TASK_STATUS_DEFAULTS = {
    "status": "queued",
    "progress_pct": 0,
    "step": "queued",
    "result": None,
    "error": None,
}


class MeetingTaskStore:
    """SQLite-backed durable store for meeting processing tasks.

    Persists meeting task progress so it survives process restarts (LLD §3.1
    W-10). Completed meeting *memory* still lives in Neo4j; this store is
    runtime task_state (see docs/NEO4J_MEMORY_CONTRACT.md) and may be rebuilt
    from the graph if deleted.
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        self._lock = threading.RLock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._db.conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS meeting_tasks (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    step TEXT NOT NULL,
                    progress_pct INTEGER NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    updated_at_ms INTEGER NOT NULL
                )
                """
            )
            self._db.conn.commit()

    def create(self, task_id: str) -> None:
        now_ms = _now_ms()
        with self._lock:
            cur = self._db.conn.cursor()
            cur.execute(
                """
                INSERT INTO meeting_tasks (
                    task_id, status, step, progress_pct, result_json, error, updated_at_ms
                ) VALUES (?, ?, ?, ?, NULL, NULL, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status=excluded.status, step=excluded.step,
                    progress_pct=excluded.progress_pct,
                    result_json=NULL, error=NULL, updated_at_ms=excluded.updated_at_ms
                """,
                (task_id, "queued", "queued", 0, now_ms),
            )
            self._db.conn.commit()

    def update(
        self,
        task_id: str,
        status: str,
        step: str,
        pct: int,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        result_json = json.dumps(result, ensure_ascii=False, default=str) if result is not None else None
        with self._lock:
            cur = self._db.conn.cursor()
            cur.execute(
                """
                INSERT INTO meeting_tasks (
                    task_id, status, step, progress_pct, result_json, error, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status=excluded.status, step=excluded.step,
                    progress_pct=excluded.progress_pct,
                    result_json=excluded.result_json, error=excluded.error,
                    updated_at_ms=excluded.updated_at_ms
                """,
                (task_id, status, step, int(pct), result_json, error, _now_ms()),
            )
            self._db.conn.commit()

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            cur = self._db.conn.cursor()
            row = cur.execute(
                "SELECT * FROM meeting_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "task_id": row["task_id"],
            "status": row["status"],
            "step": row["step"],
            "progress_pct": int(row["progress_pct"]),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error": row["error"],
        }


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)
