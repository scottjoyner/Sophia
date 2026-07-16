from __future__ import annotations

import logging
from typing import Any

from ..util.embed_text import EMBEDDING_FIELD, embedding_payload

logger = logging.getLogger(__name__)


def collect_audio_paths_from_neo4j(
    uri: str,
    user: str,
    password: str,
    *,
    speaker_node_id: str | None = None,
    speaker_name: str | None = None,
    database: str | None = None,
    limit: int = 200,
) -> list[str]:
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("neo4j driver not installed") from exc

    driver = GraphDatabase.driver(uri, auth=(user, password))
    files: list[str] = []
    with driver.session(database=database) as session:
        if speaker_node_id:
            query = (
                "MATCH (s:Speaker) WHERE id(s)=$id "
                "MATCH (s)-[:SPOKE_IN|HAS_AUDIO|RECORDED*1..2]->(a) "
                "WHERE a.path IS NOT NULL RETURN DISTINCT a.path AS path LIMIT $limit"
            )
            result = session.run(query, id=int(speaker_node_id), limit=limit)
        elif speaker_name:
            query = (
                "MATCH (s:Speaker) WHERE toLower(s.name)=toLower($name) OR toLower(s.user_id)=toLower($name) "
                "MATCH (s)-[:SPOKE_IN|HAS_AUDIO|RECORDED*1..2]->(a) "
                "WHERE a.path IS NOT NULL RETURN DISTINCT a.path AS path LIMIT $limit"
            )
            result = session.run(query, name=speaker_name, limit=limit)
        else:
            # Keyed by the in-container path so the ingested files resolve inside
            # the runtime container, and scoped to staged AudioFile nodes that are
            # still pending (already-enrolled files are skipped on re-runs).
            query = (
                "MATCH (a:AudioFile) "
                "WHERE a.storage_tier = 'ssd_staging' AND a.ingest_status = 'pending' "
                "RETURN DISTINCT a.container_path AS path LIMIT $limit"
            )
            result = session.run(query, limit=limit)
        for record in result:
            path = record.get("path")
            if path:
                files.append(path)
    driver.close()
    return files


def mark_audio_files_enrolled(
    uri: str,
    user: str,
    password: str,
    *,
    paths: list[str],
    enrolled_user_id: str,
    version_id: str,
    database: str | None = None,
) -> int:
    """Advance ``AudioFile`` nodes from ``pending`` to ``enrolled`` after a
    successful auto-ingest enrollment, recording which voiceprint owns them.

    Only ``AudioFile`` nodes currently in the ``ssd_staging`` tier with status
    ``pending`` are matched, keyed by their container-path so the in-container
    ingest sees the same identity the staging step registered. Returns the
    number of nodes advanced.
    """
    paths = [str(p) for p in paths if p]
    if not paths or not password:
        return 0
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("neo4j driver not installed") from exc

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session(database=database) as session:
            result = session.run(
                """
                MATCH (file:AudioFile)
                WHERE file.storage_tier = 'ssd_staging'
                  AND file.ingest_status = 'pending'
                  AND file.container_path IN $paths
                SET file.ingest_status = 'enrolled',
                    file.enrolled_user_id = $user_id,
                    file.enrolled_version_id = $version_id,
                    file.enrolled_at = datetime(),
                    file.updated_at = datetime()
                RETURN count(file) AS advanced
                """,
                paths=paths,
                user_id=enrolled_user_id,
                version_id=version_id,
            ).single()
        return int((result or {}).get("advanced") or 0) if result else 0
    finally:
        driver.close()


def save_capture_to_neo4j(
    uri: str,
    user: str,
    password: str,
    *,
    user_id: str,
    capture_id: str,
    transcript: str,
    audio_path: str,
    content_type: str,
    database: str | None = None,
    duration_ms: int | None = None,
    metadata: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    outbox: Any | None = None,
) -> None:
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("neo4j driver not installed") from exc

    import json

    metadata = metadata or {}
    # W-16: vector embedding for the capture (schema field EMBEDDING_FIELD).
    # Empty when no embedder is available — the vector index backfills later.
    capture_embedding = embedding_payload(transcript or "").get(EMBEDDING_FIELD)
    transcript_embedding = capture_embedding
    client_capture_id = str(metadata.get("client_capture_id") or "").strip()
    capture_dedupe_key = f"client:{client_capture_id}" if client_capture_id else f"server:{capture_id}"
    transcript_id = f"{capture_dedupe_key}:transcript"

    query = """
    MERGE (speaker:Speaker {user_id: $user_id})
      ON CREATE SET speaker.name = $user_id,
                    speaker.created_at = datetime()
    MERGE (audio:Audio {path: $audio_path})
      ON CREATE SET audio.created_at = datetime()
    SET audio.content_type = $content_type,
        audio.duration_ms = $duration_ms,
        audio.source = 'sophia_mobile_capture',
        audio.updated_at = datetime()
    MERGE (capture:SophiaCapture {dedupe_key: $capture_dedupe_key})
      ON CREATE SET capture.created_at = datetime(),
                    capture.first_capture_id = $capture_id
    SET capture.capture_id = $capture_id,
        capture.client_capture_id = $client_capture_id,
        capture.transcript = $transcript,
        capture.audio_path = $audio_path,
        capture.content_type = $content_type,
        capture.duration_ms = $duration_ms,
        capture.source = 'sophia_mobile_capture',
        capture.metadata_json = $metadata_json,
        capture.context_json = $context_json,
        capture.device_id = $device_id,
        capture.device_fingerprint = $device_fingerprint,
        capture.client_ip = $client_ip,
        capture.user_agent = $user_agent,
        capture.language = $language,
        capture.timezone = $timezone,
        capture.platform = $platform,
        capture.location_lat = $location_lat,
        capture.location_lng = $location_lng,
        capture.location_accuracy_m = $location_accuracy_m,
        capture.activity_context = $activity_context,
        capture.intent = $intent,
        capture.intent_confidence = $intent_confidence,
        capture.intent_source = $intent_source,
        capture.embedding = $embedding,
        capture.updated_at = datetime()
    MERGE (speaker)-[:RECORDED]->(audio)
    MERGE (audio)-[:CAPTURED_AS]->(capture)
    WITH speaker, audio, capture
    FOREACH (_ IN CASE WHEN $device_id <> '' THEN [1] ELSE [] END |
      MERGE (device:Device {device_id: $device_id})
        ON CREATE SET device.created_at = datetime()
      SET device.fingerprint = $device_fingerprint,
          device.user_agent = $user_agent,
          device.platform = $platform,
          device.language = $language,
          device.timezone = $timezone,
          device.last_seen_at = datetime()
      MERGE (speaker)-[:USES_DEVICE]->(device)
      MERGE (device)-[:RECORDED]->(capture)
    )
    WITH speaker, capture
    FOREACH (_ IN CASE WHEN $transcript <> '' THEN [1] ELSE [] END |
      MERGE (transcript:Transcript {id: $transcript_id})
        ON CREATE SET transcript.created_at = datetime()
      SET transcript.text = $transcript,
          transcript.source = 'sophia_mobile_capture',
          transcript.capture_dedupe_key = $capture_dedupe_key,
          transcript.embedding = $embedding,
          transcript.updated_at = datetime()
      MERGE (speaker)-[:SAID]->(transcript)
      MERGE (transcript)-[:CAPTURED_IN]->(capture)
    )
    """

    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        with driver.session(database=database) as session:
            context = context or {}
            session.run(
                query,
                user_id=user_id,
                capture_id=capture_id,
                client_capture_id=client_capture_id,
                capture_dedupe_key=capture_dedupe_key,
                transcript_id=transcript_id,
                transcript=transcript,
                audio_path=audio_path,
                content_type=content_type,
                duration_ms=duration_ms,
                metadata_json=json.dumps(metadata, ensure_ascii=False),
                context_json=json.dumps(context, ensure_ascii=False),
                device_id=str(context.get("device_id") or ""),
                device_fingerprint=str(context.get("device_fingerprint") or ""),
                client_ip=str(context.get("client_ip") or ""),
                user_agent=str(context.get("user_agent") or ""),
                language=str(context.get("language") or ""),
                timezone=str(context.get("timezone") or ""),
                platform=str(context.get("platform") or ""),
                location_lat=context.get("location_lat"),
                location_lng=context.get("location_lng"),
                location_accuracy_m=context.get("location_accuracy_m"),
                activity_context=str(context.get("activity_context") or ""),
                intent=str(context.get("intent") or ""),
                intent_confidence=context.get("intent_confidence"),
                intent_source=str(context.get("intent_source") or ""),
                embedding=capture_embedding,
            )
        driver.close()
    except Exception as exc:
        if outbox is not None:
            try:
                outbox.enqueue(
                    kind="capture",
                    idempotency_key=capture_dedupe_key,
                    payload={
                        "user_id": user_id,
                        "capture_id": capture_id,
                        "transcript": transcript,
                        "audio_path": audio_path,
                        "content_type": content_type,
                        "duration_ms": duration_ms,
                        "metadata": metadata or {},
                        "context": context or {},
                    },
                    error=f"{type(exc).__name__}: {exc}",
                )
                logger.warning("Neo4j capture write failed; enqueued to graph outbox: %s", exc)
            except Exception as oe:  # pragma: no cover - defensive
                logger.error("failed to enqueue capture to graph outbox: %s", oe)
        raise


def lookup_capture_by_client_capture_id(
    uri: str,
    user: str,
    password: str,
    *,
    client_capture_id: str,
    database: str | None = None,
) -> dict[str, Any]:
    """Look up a SophiaCapture in Neo4j by browser client_capture_id.

    Used by offline browser queue reconciliation after a graph outbox replay may
    have completed in the background.
    """
    clean = (client_capture_id or "").strip()[:128]
    if not clean:
        return {"ok": False, "found": False, "error": "client_capture_id is required"}
    if not password:
        return {"ok": False, "found": False, "error": "Neo4j password not configured", "client_capture_id": clean}
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("neo4j driver not installed") from exc

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session(database=database) as session:
            record = session.run(
                """
                MATCH (capture:SophiaCapture {dedupe_key: $dedupe_key})
                RETURN capture.capture_id AS capture_id,
                       capture.client_capture_id AS client_capture_id,
                       capture.audio_path AS audio_path,
                       capture.transcript AS transcript,
                       capture.updated_at AS updated_at
                LIMIT 1
                """,
                dedupe_key=f"client:{clean}",
            ).single()
    finally:
        driver.close()

    if not record:
        return {"ok": True, "found": False, "client_capture_id": clean}
    return {
        "ok": True,
        "found": True,
        "client_capture_id": clean,
        "capture_id": record.get("capture_id"),
        "audio_path": record.get("audio_path"),
        "transcript": record.get("transcript") or "",
        "graph_saved": True,
        "graph_pending": False,
        "updated_at": str(record.get("updated_at") or ""),
    }


def save_meeting_to_neo4j(
    uri: str,
    user: str,
    password: str,
    *,
    meeting_id: str,
    transcript: str,
    segments: list[dict],
    duration_s: float,
    num_speakers: int,
    summary: str | None = None,
    database: str | None = None,
) -> dict:
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:
        raise RuntimeError("neo4j driver not installed") from exc

    driver = GraphDatabase.driver(uri, auth=(user, password))
    segment_nodes = []
    with driver.session(database=database) as session:
        for i, seg in enumerate(segments):
            seg_id = f"{meeting_id}_seg_{i}"
            speaker_label = seg.get("name", f"Speaker {seg.get('speaker', 0) + 1}")
            # W-16: vector embedding for the segment transcript (empty if no embedder).
            seg_embedding = embedding_payload(seg.get("transcript", "")).get(EMBEDDING_FIELD)
            session.run(
                """
                MERGE (seg:MeetingSegment {id: $seg_id})
                SET seg.start_s = $start,
                    seg.end_s = $end,
                    seg.speaker = $speaker_label,
                    seg.speaker_idx = $speaker_idx,
                    seg.transcript = $transcript,
                    seg.confidence = $confidence,
                    seg.meeting_id = $meeting_id,
                    seg.segment_idx = $i,
                    seg.embedding = $embedding,
                    seg.created_at = datetime()
                """,
                seg_id=seg_id,
                start=float(seg["start"]),
                end=float(seg["end"]),
                speaker_label=speaker_label,
                speaker_idx=int(seg.get("speaker", 0)),
                transcript=seg.get("transcript", ""),
                confidence=float(seg.get("confidence", 0)),
                meeting_id=meeting_id,
                i=i,
                embedding=seg_embedding,
            )
            segment_nodes.append(seg_id)

        # W-16: vector embedding for the full meeting transcript (empty if no embedder).
        meeting_embedding = embedding_payload(transcript or "").get(EMBEDDING_FIELD)
        result = session.run(
            """
            MERGE (m:Meeting {id: $meeting_id})
            SET m.duration_s = $duration_s,
                m.num_speakers = $num_speakers,
                m.transcript = $transcript,
                m.summary = $summary,
                m.embedding = $embedding,
                m.created_at = datetime()
            RETURN m
            """,
            meeting_id=meeting_id,
            duration_s=duration_s,
            num_speakers=num_speakers,
            transcript=transcript,
            summary=summary or "",
            embedding=meeting_embedding,
        )
        result.single()

        for seg_id in segment_nodes:
            session.run(
                """
                MATCH (m:Meeting {id: $meeting_id})
                MATCH (seg:MeetingSegment {id: $seg_id})
                MERGE (m)-[:HAS_SEGMENT]->(seg)
                """,
                meeting_id=meeting_id,
                seg_id=seg_id,
            )

    driver.close()
    return {"meeting_id": meeting_id, "segment_count": len(segment_nodes)}


def list_recent_captures(
    uri: str,
    user: str,
    password: str,
    *,
    database: str | None = None,
    limit: int = 50,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return recent SophiaCapture nodes for the mobile Thoughts feed."""
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("neo4j driver not installed") from exc

    limit = max(1, min(int(limit), 200))
    user_filter = "WHERE capture.user_id = $user_id" if user_id else ""
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session(database=database) as session:
            records = session.run(
                f"""
                MATCH (capture:SophiaCapture)
                {user_filter}
                RETURN capture.capture_id AS capture_id,
                       capture.client_capture_id AS client_capture_id,
                       capture.user_id AS user_id,
                       capture.transcript AS transcript,
                       capture.duration_ms AS duration_ms,
                       capture.intent AS intent,
                       capture.device_id AS device_id,
                       capture.created_at AS created_at
                ORDER BY capture.created_at DESC
                LIMIT $limit
                """,
                limit=limit,
                user_id=user_id or "",
            ).data()
    finally:
        driver.close()

    out = []
    for r in records:
        created = r.get("created_at")
        out.append({
            "capture_id": r.get("capture_id"),
            "client_capture_id": r.get("client_capture_id") or "",
            "user_id": r.get("user_id") or "",
            "transcript": r.get("transcript") or "",
            "duration_ms": r.get("duration_ms") or 0,
            "intent": r.get("intent") or None,
            "device_id": r.get("device_id") or "",
            "created_at": str(created) if created is not None else "",
        })
    return out
