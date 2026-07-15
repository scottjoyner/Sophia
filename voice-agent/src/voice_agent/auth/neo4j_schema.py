from __future__ import annotations

from typing import Any

SOPHIA_SCHEMA_QUERIES = [
    "CREATE CONSTRAINT sophia_capture_dedupe_key IF NOT EXISTS FOR (c:SophiaCapture) REQUIRE c.dedupe_key IS UNIQUE",
    "CREATE CONSTRAINT sophia_capture_capture_id IF NOT EXISTS FOR (c:SophiaCapture) REQUIRE c.capture_id IS UNIQUE",
    "CREATE CONSTRAINT sophia_transcript_id IF NOT EXISTS FOR (t:Transcript) REQUIRE t.id IS UNIQUE",
    "CREATE CONSTRAINT sophia_speaker_user_id IF NOT EXISTS FOR (s:Speaker) REQUIRE s.user_id IS UNIQUE",
    "CREATE CONSTRAINT sophia_audio_path IF NOT EXISTS FOR (a:Audio) REQUIRE a.path IS UNIQUE",
    "CREATE CONSTRAINT sophia_device_id IF NOT EXISTS FOR (d:Device) REQUIRE d.device_id IS UNIQUE",
    "CREATE CONSTRAINT sophia_meeting_id IF NOT EXISTS FOR (m:Meeting) REQUIRE m.id IS UNIQUE",
    "CREATE CONSTRAINT sophia_meeting_segment_id IF NOT EXISTS FOR (s:MeetingSegment) REQUIRE s.id IS UNIQUE",
]


def ensure_sophia_neo4j_schema(
    uri: str,
    user: str,
    password: str,
    *,
    database: str | None = None,
) -> dict[str, Any]:
    """Install Sophia memory graph constraints/indexes.

    These constraints are important for offline capture retry safety.  The app
    uses MERGE for idempotency, but uniqueness constraints are what prevent
    duplicate memory nodes under concurrent retries.
    """
    if not password:
        return {"ok": False, "error": "Neo4j password not configured", "applied": 0, "queries": []}
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("neo4j driver not installed") from exc

    applied: list[str] = []
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session(database=database) as session:
            for query in SOPHIA_SCHEMA_QUERIES:
                session.run(query).consume()
                applied.append(query)
    finally:
        driver.close()
    return {"ok": True, "applied": len(applied), "queries": applied}
