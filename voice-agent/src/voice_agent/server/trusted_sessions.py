from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TrustedSession:
    session_key: str
    user_id: str
    session_id: str
    device_id: str
    device_fingerprint: str
    score: float
    accepted: bool
    match_source: str
    voiceprint_version_id: str
    created_at_ms: int
    expires_at_ms: int
    last_seen_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_key": self.session_key,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "device_id": self.device_id,
            "device_fingerprint": self.device_fingerprint,
            "score": self.score,
            "accepted": self.accepted,
            "match_source": self.match_source,
            "voiceprint_version_id": self.voiceprint_version_id,
            "created_at_ms": self.created_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "last_seen_ms": self.last_seen_ms,
        }


class TrustedSessionStore:
    """Durable trusted voice-auth session store backed by SQLite."""

    def __init__(self, db_path: Path, ttl_ms: int = 10 * 60 * 1000) -> None:
        self.db_path = Path(db_path)
        self.ttl_ms = ttl_ms
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trusted_voice_sessions (
                  session_key TEXT PRIMARY KEY,
                  user_id TEXT NOT NULL,
                  session_id TEXT NOT NULL,
                  device_id TEXT,
                  device_fingerprint TEXT,
                  score REAL NOT NULL,
                  accepted INTEGER NOT NULL,
                  match_source TEXT,
                  voiceprint_version_id TEXT,
                  created_at_ms INTEGER NOT NULL,
                  expires_at_ms INTEGER NOT NULL,
                  last_seen_ms INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_trusted_voice_sessions_expiry
                ON trusted_voice_sessions(expires_at_ms)
                """
            )

    @staticmethod
    def now_ms() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def make_key(user_id: str, session_id: str, device_id: str = "", device_fingerprint: str = "") -> str:
        raw = "|".join([user_id or "default", session_id or "mobile", device_id or "", device_fingerprint or ""])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def upsert(
        self,
        *,
        user_id: str,
        session_id: str,
        score: float,
        accepted: bool,
        device_id: str = "",
        device_fingerprint: str = "",
        match_source: str = "",
        voiceprint_version_id: str = "",
        ttl_ms: int | None = None,
    ) -> TrustedSession:
        now = self.now_ms()
        expires = now + (ttl_ms if ttl_ms is not None else self.ttl_ms)
        key = self.make_key(user_id, session_id, device_id, device_fingerprint)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT created_at_ms FROM trusted_voice_sessions WHERE session_key = ?",
                (key,),
            ).fetchone()
            created = int(row["created_at_ms"]) if row else now
            conn.execute(
                """
                INSERT INTO trusted_voice_sessions (
                  session_key, user_id, session_id, device_id, device_fingerprint,
                  score, accepted, match_source, voiceprint_version_id,
                  created_at_ms, expires_at_ms, last_seen_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_key) DO UPDATE SET
                  score = excluded.score,
                  accepted = excluded.accepted,
                  match_source = excluded.match_source,
                  voiceprint_version_id = excluded.voiceprint_version_id,
                  expires_at_ms = excluded.expires_at_ms,
                  last_seen_ms = excluded.last_seen_ms
                """,
                (
                    key,
                    user_id or "default",
                    session_id or "mobile",
                    device_id or "",
                    device_fingerprint or "",
                    float(score),
                    1 if accepted else 0,
                    match_source or "",
                    voiceprint_version_id or "",
                    created,
                    expires,
                    now,
                ),
            )
        return TrustedSession(
            session_key=key,
            user_id=user_id or "default",
            session_id=session_id or "mobile",
            device_id=device_id or "",
            device_fingerprint=device_fingerprint or "",
            score=float(score),
            accepted=bool(accepted),
            match_source=match_source or "",
            voiceprint_version_id=voiceprint_version_id or "",
            created_at_ms=created,
            expires_at_ms=expires,
            last_seen_ms=now,
        )

    def get(self, *, user_id: str, session_id: str, device_id: str = "", device_fingerprint: str = "") -> TrustedSession | None:
        key = self.make_key(user_id, session_id, device_id, device_fingerprint)
        now = self.now_ms()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM trusted_voice_sessions WHERE session_key = ?", (key,)).fetchone()
            if not row:
                return None
            if int(row["expires_at_ms"]) <= now or not bool(row["accepted"]):
                conn.execute("DELETE FROM trusted_voice_sessions WHERE session_key = ?", (key,))
                return None
            conn.execute(
                "UPDATE trusted_voice_sessions SET last_seen_ms = ? WHERE session_key = ?",
                (now, key),
            )
        return TrustedSession(
            session_key=row["session_key"],
            user_id=row["user_id"],
            session_id=row["session_id"],
            device_id=row["device_id"] or "",
            device_fingerprint=row["device_fingerprint"] or "",
            score=float(row["score"]),
            accepted=bool(row["accepted"]),
            match_source=row["match_source"] or "",
            voiceprint_version_id=row["voiceprint_version_id"] or "",
            created_at_ms=int(row["created_at_ms"]),
            expires_at_ms=int(row["expires_at_ms"]),
            last_seen_ms=now,
        )

    def clear(self, *, user_id: str, session_id: str, device_id: str = "", device_fingerprint: str = "") -> bool:
        key = self.make_key(user_id, session_id, device_id, device_fingerprint)
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM trusted_voice_sessions WHERE session_key = ?", (key,))
            return cur.rowcount > 0

    def prune_expired(self) -> int:
        now = self.now_ms()
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM trusted_voice_sessions WHERE expires_at_ms <= ?", (now,))
            return cur.rowcount
