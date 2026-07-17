from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any


class RelayStore:
    """SQLite-backed durable state store for Tommy relay sessions."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._init()

    def _init(self) -> None:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS relay_devices (
                    device_id TEXT PRIMARY KEY,
                    name TEXT,
                    owner_id TEXT,
                    capabilities_json TEXT,
                    platform TEXT,
                    mesh_node TEXT,
                    status TEXT,
                    last_seen_ms INTEGER,
                    created_at_ms INTEGER,
                    updated_at_ms INTEGER,
                    token_hash TEXT,
                    fallback_priority INTEGER DEFAULT 100,
                    trusted INTEGER DEFAULT 0
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS relay_sessions (
                    session_id TEXT PRIMARY KEY,
                    owner_id TEXT,
                    active_device_id TEXT,
                    state TEXT,
                    last_seq INTEGER,
                    created_at_ms INTEGER,
                    updated_at_ms INTEGER,
                    resume_token_hash TEXT,
                    expected_seq INTEGER DEFAULT 0,
                    missing_ranges_json TEXT DEFAULT '[]'
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS relay_leases (
                    session_id TEXT PRIMARY KEY,
                    device_id TEXT,
                    lease_token TEXT,
                    lease_token_hash TEXT,
                    lease_version INTEGER,
                    lease_expires_at_ms INTEGER,
                    updated_at_ms INTEGER,
                    revoked_at_ms INTEGER,
                    revoked_reason TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS relay_audio_chunks (
                    session_id TEXT,
                    device_id TEXT,
                    seq INTEGER,
                    encoding TEXT,
                    byte_count INTEGER,
                    ts_ms INTEGER,
                    PRIMARY KEY (session_id, device_id, seq)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS relay_transcripts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    device_id TEXT,
                    seq INTEGER,
                    text TEXT,
                    partial INTEGER,
                    source TEXT,
                    metadata_json TEXT,
                    ts_ms INTEGER,
                    queued_at_ms INTEGER,
                    processed_at_ms INTEGER,
                    response_text TEXT,
                    error TEXT,
                    UNIQUE(session_id, device_id, seq, source, partial)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS relay_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT,
                    session_id TEXT,
                    device_id TEXT,
                    payload_json TEXT,
                    ts_ms INTEGER
                )
                """
            )
            self._ensure_columns(
                "relay_devices",
                {
                    "token_hash": "TEXT",
                    "fallback_priority": "INTEGER DEFAULT 100",
                    "trusted": "INTEGER DEFAULT 0",
                },
            )
            self._ensure_columns(
                "relay_sessions",
                {
                    "resume_token_hash": "TEXT",
                    "expected_seq": "INTEGER DEFAULT 0",
                    "missing_ranges_json": "TEXT DEFAULT '[]'",
                },
            )
            self._ensure_columns(
                "relay_leases",
                {
                    "lease_token_hash": "TEXT",
                    "revoked_at_ms": "INTEGER",
                    "revoked_reason": "TEXT",
                },
            )
            self._ensure_columns(
                "relay_transcripts",
                {
                    "queued_at_ms": "INTEGER",
                    "processed_at_ms": "INTEGER",
                    "response_text": "TEXT",
                    "error": "TEXT",
                },
            )
            self.conn.commit()

    def _ensure_columns(self, table: str, columns: dict[str, str]) -> None:
        allowed_specs = {
            "relay_devices": {"token_hash": "TEXT", "fallback_priority": "INTEGER DEFAULT 100", "trusted": "INTEGER DEFAULT 0"},
            "relay_sessions": {"resume_token_hash": "TEXT", "expected_seq": "INTEGER DEFAULT 0", "missing_ranges_json": "TEXT DEFAULT '[]'"},
            "relay_leases": {"lease_token_hash": "TEXT", "revoked_at_ms": "INTEGER", "revoked_reason": "TEXT"},
            "relay_transcripts": {"queued_at_ms": "INTEGER", "processed_at_ms": "INTEGER", "response_text": "TEXT", "error": "TEXT"},
        }
        if allowed_specs.get(table) != columns:
            raise ValueError(f"Unsupported relay schema migration target: {table}")
        quoted_table = self._quote_identifier(table, allowed_specs.keys())
        existing = {row["name"] for row in self.conn.execute("PRAGMA table_info(" + quoted_table + ")").fetchall()}
        for column, spec in columns.items():
            if column not in existing:
                quoted_column = self._quote_identifier(column, columns.keys())
                self.conn.execute("ALTER TABLE " + quoted_table + " ADD COLUMN " + quoted_column + " " + spec)

    @staticmethod
    def _quote_identifier(identifier: str, allowed: Iterable[str]) -> str:
        if identifier not in allowed:
            raise ValueError(f"Unsupported relay schema identifier: {identifier}")
        return '"' + identifier.replace('"', '""') + '"'

    def upsert_device(self, record: dict[str, Any]) -> dict[str, Any]:
        now = record["last_seen_ms"]
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                """
                INSERT INTO relay_devices (
                    device_id, name, owner_id, capabilities_json, platform, mesh_node,
                    status, last_seen_ms, created_at_ms, updated_at_ms, token_hash,
                    fallback_priority, trusted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    name=excluded.name,
                    owner_id=excluded.owner_id,
                    capabilities_json=excluded.capabilities_json,
                    platform=excluded.platform,
                    mesh_node=excluded.mesh_node,
                    status=excluded.status,
                    last_seen_ms=excluded.last_seen_ms,
                    updated_at_ms=excluded.updated_at_ms,
                    token_hash=COALESCE(excluded.token_hash, relay_devices.token_hash),
                    fallback_priority=excluded.fallback_priority,
                    trusted=excluded.trusted
                """,
                (
                    record["device_id"],
                    record.get("name", ""),
                    record.get("owner_id", "scott"),
                    json.dumps(record.get("capabilities", [])),
                    record.get("platform", ""),
                    record.get("mesh_node", ""),
                    record.get("status", "online"),
                    now,
                    now,
                    now,
                    record.get("token_hash"),
                    record.get("fallback_priority", 100),
                    1 if record.get("trusted") else 0,
                ),
            )
            self.conn.commit()
        return self.get_device(record["device_id"]) or record

    def get_device(self, device_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM relay_devices WHERE device_id=?", (device_id,)).fetchone()
        return self._device_from_row(row) if row else None

    def update_device_fields(self, device_id: str, fields: dict[str, Any], *, now_ms: int) -> dict[str, Any] | None:
        if not fields:
            return self.get_device(device_id)
        assignments = [f"{key}=?" for key in fields]
        params = list(fields.values()) + [now_ms, device_id]
        with self._lock:
            self.conn.execute(
                f"UPDATE relay_devices SET {', '.join(assignments)}, updated_at_ms=? WHERE device_id=?",
                params,
            )
            self.conn.commit()
        return self.get_device(device_id)

    def list_devices(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute("SELECT * FROM relay_devices ORDER BY updated_at_ms DESC").fetchall()
        return [self._device_from_row(row) for row in rows]

    def candidate_fallback_devices(self, *, owner_id: str, exclude_device_id: str | None = None, now_ms: int | None = None, stale_ms: int = 30_000) -> list[dict[str, Any]]:
        devices = self.list_devices()
        candidates = []
        for device in devices:
            if device.get("status") == "revoked":
                continue
            if not device.get("trusted"):
                continue
            if device.get("owner_id") != owner_id:
                continue
            if exclude_device_id and device.get("device_id") == exclude_device_id:
                continue
            if "mic" not in device.get("capabilities", []) and "browser_audio" not in device.get("capabilities", []):
                continue
            if now_ms is not None and device.get("last_seen_ms") and now_ms - int(device["last_seen_ms"]) > stale_ms:
                continue
            candidates.append(device)
        return sorted(candidates, key=lambda d: (d.get("fallback_priority", 100), -(d.get("last_seen_ms") or 0)))

    def upsert_session(self, session: dict[str, Any]) -> dict[str, Any]:
        now = session["updated_at_ms"]
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO relay_sessions (
                    session_id, owner_id, active_device_id, state, last_seq, created_at_ms,
                    updated_at_ms, resume_token_hash, expected_seq, missing_ranges_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    owner_id=excluded.owner_id,
                    active_device_id=excluded.active_device_id,
                    state=excluded.state,
                    last_seq=COALESCE(excluded.last_seq, relay_sessions.last_seq),
                    updated_at_ms=excluded.updated_at_ms,
                    resume_token_hash=COALESCE(excluded.resume_token_hash, relay_sessions.resume_token_hash),
                    expected_seq=COALESCE(excluded.expected_seq, relay_sessions.expected_seq),
                    missing_ranges_json=COALESCE(excluded.missing_ranges_json, relay_sessions.missing_ranges_json)
                """,
                (
                    session["session_id"],
                    session.get("owner_id", "scott"),
                    session.get("active_device_id"),
                    session.get("state", "listening"),
                    session.get("last_seq"),
                    now,
                    now,
                    session.get("resume_token_hash"),
                    session.get("expected_seq"),
                    json.dumps(session.get("missing_ranges", [])),
                ),
            )
            self.conn.commit()
        return self.get_session(session["session_id"]) or session

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM relay_sessions WHERE session_id=?", (session_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        data["missing_ranges"] = json.loads(data.pop("missing_ranges_json") or "[]")
        return data

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute("SELECT * FROM relay_sessions ORDER BY updated_at_ms DESC").fetchall()
        sessions = []
        for row in rows:
            data = dict(row)
            data["missing_ranges"] = json.loads(data.pop("missing_ranges_json") or "[]")
            sessions.append(data)
        return sessions

    def save_lease(self, lease: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            previous = self.get_lease(lease["session_id"])
            version = lease.get("lease_version") or ((previous or {}).get("lease_version", 0) + 1)
            self.conn.execute(
                """
                INSERT INTO relay_leases (
                    session_id, device_id, lease_token, lease_token_hash, lease_version,
                    lease_expires_at_ms, updated_at_ms, revoked_at_ms, revoked_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    device_id=excluded.device_id,
                    lease_token=excluded.lease_token,
                    lease_token_hash=excluded.lease_token_hash,
                    lease_version=excluded.lease_version,
                    lease_expires_at_ms=excluded.lease_expires_at_ms,
                    updated_at_ms=excluded.updated_at_ms,
                    revoked_at_ms=excluded.revoked_at_ms,
                    revoked_reason=excluded.revoked_reason
                """,
                (
                    lease["session_id"],
                    lease["device_id"],
                    lease.get("lease_token"),
                    lease.get("lease_token_hash"),
                    version,
                    lease["lease_expires_at_ms"],
                    lease["updated_at_ms"],
                    lease.get("revoked_at_ms"),
                    lease.get("revoked_reason"),
                ),
            )
            self.conn.commit()
        return self.get_lease(lease["session_id"]) or lease

    def revoke_lease(self, session_id: str, *, now_ms: int, reason: str) -> dict[str, Any] | None:
        lease = self.get_lease(session_id)
        if not lease:
            return None
        with self._lock:
            self.conn.execute(
                "UPDATE relay_leases SET revoked_at_ms=?, revoked_reason=?, updated_at_ms=? WHERE session_id=?",
                (now_ms, reason, now_ms, session_id),
            )
            self.conn.commit()
        return self.get_lease(session_id)

    def get_lease(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM relay_leases WHERE session_id=?", (session_id,)).fetchone()
        return dict(row) if row else None

    def record_audio_chunk(self, record: dict[str, Any], *, expected_seq: int | None = None, missing_ranges: list[list[int]] | None = None) -> bool:
        with self._lock:
            try:
                self.conn.execute(
                    """
                    INSERT INTO relay_audio_chunks (session_id, device_id, seq, encoding, byte_count, ts_ms)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["session_id"],
                        record["device_id"],
                        record["seq"],
                        record.get("encoding", ""),
                        record.get("byte_count", 0),
                        record["ts_ms"],
                    ),
                )
            except sqlite3.IntegrityError:
                return False
            update_parts = ["last_seq=MAX(COALESCE(last_seq, -1), ?)", "updated_at_ms=?"]
            params: list[Any] = [record["seq"], record["ts_ms"]]
            if expected_seq is not None:
                update_parts.append("expected_seq=?")
                params.append(expected_seq)
            if missing_ranges is not None:
                update_parts.append("missing_ranges_json=?")
                params.append(json.dumps(missing_ranges))
            params.append(record["session_id"])
            self.conn.execute(
                f"UPDATE relay_sessions SET {', '.join(update_parts)} WHERE session_id=?",
                params,
            )
            self.conn.commit()
        return True

    def record_transcript(self, record: dict[str, Any]) -> int | None:
        with self._lock:
            try:
                cur = self.conn.execute(
                    """
                    INSERT INTO relay_transcripts (
                        session_id, device_id, seq, text, partial, source, metadata_json, ts_ms, queued_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["session_id"],
                        record["device_id"],
                        record["seq"],
                        record.get("text", ""),
                        1 if record.get("partial") else 0,
                        record.get("source", "stt"),
                        json.dumps(record.get("metadata", {})),
                        record["ts_ms"],
                        record.get("queued_at_ms"),
                    ),
                )
            except sqlite3.IntegrityError:
                return None
            self.conn.commit()
            return int(cur.lastrowid)

    def update_transcript_result(self, transcript_id: int, *, processed_at_ms: int, response_text: str | None = None, error: str | None = None) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE relay_transcripts SET processed_at_ms=?, response_text=?, error=? WHERE id=?",
                (processed_at_ms, response_text, error, transcript_id),
            )
            self.conn.commit()

    def list_transcripts(self, session_id: str, *, after_seq: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM relay_transcripts WHERE session_id=?"
        params: list[Any] = [session_id]
        if after_seq is not None:
            query += " AND seq > ?"
            params.append(after_seq)
        query += " ORDER BY seq ASC, id ASC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self.conn.execute(query, params).fetchall()
        return [self._transcript_from_row(row) for row in rows]

    def log_event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO relay_events (type, session_id, device_id, payload_json, ts_ms) VALUES (?, ?, ?, ?, ?)",
                (
                    event_type,
                    payload.get("session_id"),
                    payload.get("device_id") or payload.get("active_device_id") or payload.get("to_device_id"),
                    json.dumps(payload, ensure_ascii=False),
                    payload.get("ts_ms"),
                ),
            )
            self.conn.commit()
            event_id = cur.lastrowid
        return {"id": event_id, "type": event_type, "payload": payload}

    def list_events(self, after_id: int = 0, session_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        query = "SELECT * FROM relay_events WHERE id > ?"
        params: list[Any] = [after_id]
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        query += " ORDER BY id ASC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self.conn.execute(query, params).fetchall()
        return [{"id": row["id"], "type": row["type"], "payload": json.loads(row["payload_json"])} for row in rows]

    @staticmethod
    def _device_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["capabilities"] = json.loads(data.pop("capabilities_json") or "[]")
        data["trusted"] = bool(data.get("trusted"))
        return data

    @staticmethod
    def _transcript_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["metadata"] = json.loads(data.pop("metadata_json") or "{}")
        data["partial"] = bool(data.get("partial"))
        return data
