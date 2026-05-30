from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List


@dataclass(frozen=True)
class GraphOutboxItem:
    id: str
    kind: str
    idempotency_key: str
    payload: Dict[str, Any]
    status: str
    attempts: int
    next_attempt_ms: int
    created_at_ms: int
    updated_at_ms: int
    last_error: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "idempotency_key": self.idempotency_key,
            "payload": self.payload,
            "status": self.status,
            "attempts": self.attempts,
            "next_attempt_ms": self.next_attempt_ms,
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
            "last_error": self.last_error,
        }


class GraphOutbox:
    """SQLite outbox for writes that must be replayed into Neo4j.

    This is not a memory store. It is a local recovery journal for graph writes
    that could not be committed to Neo4j at request time.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @staticmethod
    def now_ms() -> int:
        return int(time.time() * 1000)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_graph_writes (
                  id TEXT PRIMARY KEY,
                  kind TEXT NOT NULL,
                  idempotency_key TEXT NOT NULL UNIQUE,
                  payload_json TEXT NOT NULL,
                  status TEXT NOT NULL,
                  attempts INTEGER NOT NULL,
                  next_attempt_ms INTEGER NOT NULL,
                  created_at_ms INTEGER NOT NULL,
                  updated_at_ms INTEGER NOT NULL,
                  last_error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pending_graph_writes_status_due
                ON pending_graph_writes(status, next_attempt_ms)
                """
            )

    def enqueue(self, *, kind: str, idempotency_key: str, payload: Dict[str, Any], error: str = "") -> GraphOutboxItem:
        now = self.now_ms()
        item_id = uuid.uuid4().hex
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO pending_graph_writes (
                  id, kind, idempotency_key, payload_json, status, attempts,
                  next_attempt_ms, created_at_ms, updated_at_ms, last_error
                ) VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?)
                ON CONFLICT(idempotency_key) DO UPDATE SET
                  payload_json = excluded.payload_json,
                  status = CASE
                    WHEN pending_graph_writes.status = 'succeeded' THEN pending_graph_writes.status
                    ELSE 'pending'
                  END,
                  next_attempt_ms = excluded.next_attempt_ms,
                  updated_at_ms = excluded.updated_at_ms,
                  last_error = excluded.last_error
                """,
                (item_id, kind, idempotency_key, payload_json, now, now, now, error or ""),
            )
        existing = self.get_by_key(idempotency_key)
        if existing is None:  # defensive; should not happen
            raise RuntimeError(f"Failed to enqueue graph outbox item {idempotency_key}")
        return existing

    def get_by_key(self, idempotency_key: str) -> GraphOutboxItem | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pending_graph_writes WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return self._row_to_item(row) if row else None

    def list_due(self, *, limit: int = 25, now_ms: int | None = None) -> List[GraphOutboxItem]:
        now = self.now_ms() if now_ms is None else now_ms
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM pending_graph_writes
                WHERE status IN ('pending', 'retry') AND next_attempt_ms <= ?
                ORDER BY created_at_ms ASC
                LIMIT ?
                """,
                (now, limit),
            ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def mark_succeeded(self, item_id: str) -> None:
        now = self.now_ms()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE pending_graph_writes
                SET status = 'succeeded', updated_at_ms = ?, last_error = ''
                WHERE id = ?
                """,
                (now, item_id),
            )

    def mark_failed(self, item_id: str, error: str, *, base_delay_ms: int = 30_000, max_delay_ms: int = 30 * 60 * 1000) -> None:
        now = self.now_ms()
        with self._connect() as conn:
            row = conn.execute("SELECT attempts FROM pending_graph_writes WHERE id = ?", (item_id,)).fetchone()
            attempts = int(row["attempts"]) + 1 if row else 1
            delay = min(max_delay_ms, base_delay_ms * (2 ** max(0, attempts - 1)))
            conn.execute(
                """
                UPDATE pending_graph_writes
                SET status = 'retry', attempts = ?, next_attempt_ms = ?, updated_at_ms = ?, last_error = ?
                WHERE id = ?
                """,
                (attempts, now + delay, now, error[:1000], item_id),
            )

    def counts(self) -> Dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, count(*) AS n FROM pending_graph_writes GROUP BY status"
            ).fetchall()
        return {str(row["status"]): int(row["n"]) for row in rows}

    def _row_to_item(self, row: sqlite3.Row) -> GraphOutboxItem:
        return GraphOutboxItem(
            id=row["id"],
            kind=row["kind"],
            idempotency_key=row["idempotency_key"],
            payload=json.loads(row["payload_json"]),
            status=row["status"],
            attempts=int(row["attempts"]),
            next_attempt_ms=int(row["next_attempt_ms"]),
            created_at_ms=int(row["created_at_ms"]),
            updated_at_ms=int(row["updated_at_ms"]),
            last_error=row["last_error"] or "",
        )


SUPPORTED_OUTBOX_KINDS = {"capture"}


def replay_graph_outbox_items(
    outbox: GraphOutbox,
    *,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    neo4j_database: str | None = None,
    limit: int = 25,
) -> Dict[str, Any]:
    """Replay due outbox items to Neo4j.

    Currently supports capture writes. Additional kinds should be added only when
    their completed memory model is Neo4j-first.
    """
    if not neo4j_password:
        return {"ok": False, "reason": "Neo4j password not configured", "processed": 0, "succeeded": 0, "failed": 0}

    from ..auth.neo4j_ingest import save_capture_to_neo4j

    processed = succeeded = failed = 0
    errors: List[Dict[str, str]] = []
    for item in outbox.list_due(limit=limit):
        processed += 1
        try:
            if item.kind not in SUPPORTED_OUTBOX_KINDS:
                raise RuntimeError(f"Unsupported graph outbox kind: {item.kind}")
            if item.kind == "capture":
                p = item.payload
                save_capture_to_neo4j(
                    neo4j_uri,
                    neo4j_user,
                    neo4j_password,
                    user_id=p["user_id"],
                    capture_id=p["capture_id"],
                    transcript=p.get("transcript", ""),
                    audio_path=p.get("audio_path", ""),
                    content_type=p.get("content_type", ""),
                    database=neo4j_database,
                    duration_ms=p.get("duration_ms"),
                    metadata=p.get("metadata") or {},
                    context=p.get("context") or {},
                )
            outbox.mark_succeeded(item.id)
            succeeded += 1
        except Exception as exc:  # pragma: no cover - exercised with mocked integrations later
            failed += 1
            error = f"{type(exc).__name__}: {exc}"
            outbox.mark_failed(item.id, error)
            errors.append({"id": item.id, "kind": item.kind, "error": error})
    return {"ok": failed == 0, "processed": processed, "succeeded": succeeded, "failed": failed, "errors": errors}
