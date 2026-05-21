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

    driver = GraphDatabase.driver(uri, auth=(user, password))
    query = """
    MERGE (speaker:Speaker {user_id: $user_id})
      ON CREATE SET speaker.name = $user_id
    MERGE (audio:Audio {path: $audio_path})
      ON CREATE SET audio.created_at = datetime()
    SET audio.content_type = $content_type,
        audio.duration_ms = $duration_ms,
        audio.source = 'sophia_mobile_capture'
    CREATE (capture:SophiaCapture {
        capture_id: $capture_id,
        transcript: $transcript,
        audio_path: $audio_path,
        content_type: $content_type,
        duration_ms: $duration_ms,
        source: 'sophia_mobile_capture',
        metadata_json: $metadata_json,
        context_json: $context_json,
        device_id: $device_id,
        device_fingerprint: $device_fingerprint,
        client_ip: $client_ip,
        user_agent: $user_agent,
        language: $language,
        timezone: $timezone,
        platform: $platform,
        location_lat: $location_lat,
        location_lng: $location_lng,
        location_accuracy_m: $location_accuracy_m,
        activity_context: $activity_context,
        intent: $intent,
        intent_confidence: $intent_confidence,
        intent_source: $intent_source,
        created_at: datetime()
    })
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
      CREATE (transcript:Transcript {
          text: $transcript,
          source: 'sophia_mobile_capture',
          created_at: datetime()
      })
      CREATE (speaker)-[:SAID]->(transcript)
      CREATE (transcript)-[:CAPTURED_IN]->(capture)
    )
    """
    import json

    with driver.session(database=database) as session:
        context = context or {}
        session.run(
            query,
            user_id=user_id,
            capture_id=capture_id,
            transcript=transcript,
            audio_path=audio_path,
            content_type=content_type,
            duration_ms=duration_ms,
            metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
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
