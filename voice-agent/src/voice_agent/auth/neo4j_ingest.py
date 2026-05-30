from __future__ import annotations

from typing import Any, Dict, List, Optional


def collect_audio_paths_from_neo4j(
    uri: str,
    user: str,
    password: str,
    *,
    speaker_node_id: Optional[str] = None,
    speaker_name: Optional[str] = None,
    database: Optional[str] = None,
    limit: int = 200,
) -> List[str]:
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("neo4j driver not installed") from exc

    driver = GraphDatabase.driver(uri, auth=(user, password))
    files: List[str] = []
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
            query = "MATCH (a) WHERE a.path IS NOT NULL RETURN DISTINCT a.path AS path LIMIT $limit"
            result = session.run(query, limit=limit)
        for record in result:
            path = record.get("path")
            if path:
                files.append(path)
    driver.close()
    return files


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
    database: Optional[str] = None,
    duration_ms: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("neo4j driver not installed") from exc

    import json

    metadata = metadata or {}
    client_capture_id = str(metadata.get("client_capture_id") or "").strip()
    capture_dedupe_key = f"client:{client_capture_id}" if client_capture_id else f"server:{capture_id}"
    transcript_id = f"{capture_dedupe_key}:transcript"

    driver = GraphDatabase.driver(uri, auth=(user, password))
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
          transcript.updated_at = datetime()
      MERGE (speaker)-[:SAID]->(transcript)
      MERGE (transcript)-[:CAPTURED_IN]->(capture)
    )
    """

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
        )
    driver.close()


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
            )
            segment_nodes.append(seg_id)

        result = session.run(
            """
            MERGE (m:Meeting {id: $meeting_id})
            SET m.duration_s = $duration_s,
                m.num_speakers = $num_speakers,
                m.transcript = $transcript,
                m.summary = $summary,
                m.created_at = datetime()
            RETURN m
            """,
            meeting_id=meeting_id,
            duration_s=duration_s,
            num_speakers=num_speakers,
            transcript=transcript,
            summary=summary or "",
        )
        meeting_node = result.single()

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
