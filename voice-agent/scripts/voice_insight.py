#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List

import numpy as np
import yaml

from voice_agent.auth.speaker_embedder import SpeakerEmbedder
from voice_agent.util.audio import read_wav, write_wav


def load_config(path: str) -> Dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text()) or {}
    neo4j = data.setdefault("neo4j", {})
    if os.getenv("NEO4J_URI"):
        neo4j["uri"] = os.environ["NEO4J_URI"]
    if os.getenv("NEO4J_USER"):
        neo4j["user"] = os.environ["NEO4J_USER"]
    if os.getenv("NEO4J_PASSWORD"):
        neo4j["password"] = os.environ["NEO4J_PASSWORD"]
    if os.getenv("NEO4J_SOURCE_DATABASE"):
        neo4j["source_database"] = os.environ["NEO4J_SOURCE_DATABASE"]
    if os.getenv("NEO4J_DATABASE"):
        neo4j["target_database"] = os.environ["NEO4J_DATABASE"]
    return data


def driver_from_config(config: Dict[str, Any]):
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:
        raise RuntimeError("neo4j package is required") from exc
    neo4j = config["neo4j"]
    password = neo4j.get("password")
    if not password:
        raise RuntimeError("Neo4j password required: set NEO4J_PASSWORD")
    return GraphDatabase.driver(neo4j.get("uri", "bolt://localhost:7687"), auth=(neo4j.get("user", "neo4j"), password))


def source_db(config: Dict[str, Any]) -> str:
    return config.get("neo4j", {}).get("source_database", "neo4j")


def target_db(config: Dict[str, Any]) -> str:
    return config.get("neo4j", {}).get("target_database", "memory")


def namespace(config: Dict[str, Any]) -> str:
    return config.get("voice_insight", {}).get("namespace", "sophia_voice_insight_v1")


def init_schema(config: Dict[str, Any]) -> None:
    queries = [
        "CREATE CONSTRAINT voice_identity_id IF NOT EXISTS FOR (n:VoiceIdentity) REQUIRE n.identity_id IS UNIQUE",
        "CREATE CONSTRAINT voice_cluster_id IF NOT EXISTS FOR (n:VoiceSpeakerCluster) REQUIRE n.cluster_id IS UNIQUE",
        "CREATE CONSTRAINT voice_recording_id IF NOT EXISTS FOR (n:VoiceRecording) REQUIRE n.recording_id IS UNIQUE",
        "CREATE CONSTRAINT voice_segment_id IF NOT EXISTS FOR (n:VoiceSegment) REQUIRE n.segment_id IS UNIQUE",
        "CREATE CONSTRAINT voice_utterance_id IF NOT EXISTS FOR (n:VoiceUtterance) REQUIRE n.utterance_id IS UNIQUE",
        "CREATE CONSTRAINT voice_training_sample_id IF NOT EXISTS FOR (n:VoiceTrainingSample) REQUIRE n.sample_id IS UNIQUE",
        "CREATE CONSTRAINT voiceprint_id IF NOT EXISTS FOR (n:Voiceprint) REQUIRE n.voiceprint_id IS UNIQUE",
        "CREATE INDEX voice_segment_identity IF NOT EXISTS FOR (n:VoiceSegment) ON (n.identity_id)",
        "CREATE INDEX voice_segment_recording IF NOT EXISTS FOR (n:VoiceSegment) ON (n.recording_id)",
        "CREATE INDEX voice_utterance_identity IF NOT EXISTS FOR (n:VoiceUtterance) ON (n.identity_id)",
        "CREATE INDEX voice_training_identity IF NOT EXISTS FOR (n:VoiceTrainingSample) ON (n.identity_id)",
    ]
    driver = driver_from_config(config)
    with driver.session(database=target_db(config)) as session:
        for query in queries:
            session.run(query).consume()
    driver.close()


def legacy_speaker_report(config: Dict[str, Any], limit: int, recording_key: str | None = None) -> List[Dict[str, Any]]:
    match = "MATCH (sp:Speaker)<-[:SPOKEN_BY]-(seg:Segment)<-[:HAS_SEGMENT]-(tx:Transcription)"
    where = "WHERE tx.key = $recording_key" if recording_key else ""
    query = """
    {match}
    {where}
    WITH sp, tx.key AS recording_key, sp.key AS speaker_key, coalesce(sp.label, seg.speaker_label, 'UNKNOWN') AS label,
         count(seg) AS segments,
         sum(coalesce(seg.end, 0.0) - coalesce(seg.start, 0.0)) AS seconds,
         collect(seg.text)[0..3] AS samples
    RETURN speaker_key, label, recording_key, segments, round(seconds, 2) AS seconds, samples
    ORDER BY seconds DESC
    LIMIT $limit
    """.format(match=match, where=where)
    driver = driver_from_config(config)
    with driver.session(database=source_db(config)) as session:
        rows = [dict(record) for record in session.run(query, limit=limit, recording_key=recording_key)]
    driver.close()
    return rows


def seed_legacy_speaker(config: Dict[str, Any], identity: str, speaker_key: str, label: str, confidence: float) -> None:
    cluster_id = f"legacy:{speaker_key}:{label}"
    query = """
    MERGE (identity:VoiceIdentity {identity_id: $identity})
      ON CREATE SET identity.created_at = datetime()
    SET identity.name = $identity,
        identity.updated_at = datetime()
    MERGE (cluster:VoiceSpeakerCluster {cluster_id: $cluster_id})
      ON CREATE SET cluster.created_at = datetime()
    SET cluster.source = 'legacy_neo4j',
        cluster.legacy_key = $speaker_key,
        cluster.legacy_label = $label,
        cluster.updated_at = datetime()
    MERGE (cluster)-[r:IDENTIFIES_AS]->(identity)
    SET r.confidence = $confidence,
        r.source = 'manual_seed',
        r.updated_at = datetime()
    """
    driver = driver_from_config(config)
    with driver.session(database=target_db(config)) as session:
        session.run(query, identity=identity, cluster_id=cluster_id, speaker_key=speaker_key, label=label, confidence=confidence).consume()
    driver.close()


def storage_config(config: Dict[str, Any]) -> Dict[str, Any]:
    return config.get("voice_insight", {}).get("storage", {})


def training_config(config: Dict[str, Any]) -> Dict[str, Any]:
    return config.get("voice_insight", {}).get("training", {})


def rewrite_path(config: Dict[str, Any], path: str | None) -> str | None:
    if not path:
        return None
    rewritten = path
    for rule in storage_config(config).get("path_rewrites", []) or []:
        src = str(rule.get("from") or "")
        dst = str(rule.get("to") or "")
        if src and rewritten.startswith(src):
            rewritten = dst + rewritten[len(src) :]
            break
    return rewritten


def host_training_root(config: Dict[str, Any]) -> str:
    storage = storage_config(config)
    return str(storage.get("host_training_root") or storage.get("training_root") or "voice-insight/training")


def container_training_root(config: Dict[str, Any]) -> str:
    storage = storage_config(config)
    return str(storage.get("training_root") or host_training_root(config))


def host_sample_path(config: Dict[str, Any], container_path: str) -> str:
    storage = storage_config(config)
    host_root = str(storage.get("host_training_root") or "")
    cont_root = str(storage.get("training_root") or "")
    if host_root and cont_root and container_path.startswith(cont_root):
        return host_root + container_path[len(cont_root) :]
    return container_path


def sample_id_for(identity: str, legacy_segment_id: str) -> str:
    raw = f"{identity}:{legacy_segment_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def configured_seed_keys(config: Dict[str, Any], identity: str | None = None) -> List[Dict[str, Any]]:
    seeds = config.get("voice_insight", {}).get("legacy", {}).get("speaker_seeds", []) or []
    if identity:
        seeds = [seed for seed in seeds if seed.get("identity") == identity]
    return seeds


def _legacy_training_candidate_rows(config: Dict[str, Any], identity: str, limit: int) -> List[Dict[str, Any]]:
    seeds = configured_seed_keys(config, identity)
    seed_pairs = [{"key": seed["key"], "label": seed["label"], "confidence": float(seed.get("confidence", 0.9))} for seed in seeds]
    if not seed_pairs:
        return []
    train_cfg = training_config(config)
    min_seconds = float(train_cfg.get("min_segment_seconds", 2.0))
    max_seconds = float(train_cfg.get("max_segment_seconds", 18.0))
    min_text_chars = int(train_cfg.get("min_text_chars", 16))
    query = """
    UNWIND $seed_pairs AS seed
    MATCH (tx:Transcription {key: seed.key})-[:HAS_SEGMENT]->(seg:Segment)
    OPTIONAL MATCH (seg)-[:SPOKEN_BY]->(sp:Speaker)
    WITH seed, tx, seg, sp, coalesce(sp.label, seg.speaker_label, 'UNKNOWN') AS label
    WHERE label = seed.label
      AND seg.start IS NOT NULL
      AND seg.end IS NOT NULL
      AND (seg.end - seg.start) >= $min_seconds
      AND (seg.end - seg.start) <= $max_seconds
      AND seg.text IS NOT NULL
      AND size(trim(seg.text)) >= $min_text_chars
      AND NOT trim(seg.text) STARTS WITH 'I-104'
    RETURN tx.key AS recording_key,
           coalesce(tx.source_media, tx.source_media_prev) AS source_media,
           sp.key AS speaker_key,
           label AS speaker_label,
           seed.confidence AS seed_confidence,
           seg.id AS legacy_segment_id,
           seg.start AS start,
           seg.end AS end,
           seg.text AS text
    ORDER BY tx.key, seg.start
    LIMIT $limit
    """
    driver = driver_from_config(config)
    with driver.session(database=source_db(config)) as session:
        rows = [
            dict(record)
            for record in session.run(
                query,
                seed_pairs=seed_pairs,
                min_seconds=min_seconds,
                max_seconds=max_seconds,
                min_text_chars=min_text_chars,
                limit=limit,
            )
        ]
    driver.close()
    deduped = {}
    for row in rows:
        row["identity_id"] = identity
        row["source_media_resolved"] = rewrite_path(config, row.get("source_media"))
        deduped.setdefault(row.get("legacy_segment_id"), row)
    return list(deduped.values())


def collect_training_candidates(config: Dict[str, Any], identity: str, limit: int) -> List[Dict[str, Any]]:
    return _legacy_training_candidate_rows(config, identity, limit)


def _register_training_samples(config: Dict[str, Any], rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    ns = namespace(config)
    query = """
    UNWIND $rows AS row
    MERGE (identity:VoiceIdentity {identity_id: row.identity_id})
      ON CREATE SET identity.created_at = datetime()
    SET identity.name = row.identity_id,
        identity.updated_at = datetime()
    MERGE (sample:VoiceTrainingSample {sample_id: row.sample_id})
      ON CREATE SET sample.created_at = datetime()
    SET sample.identity_id = row.identity_id,
        sample.recording_id = $ns + ':legacy:' + row.recording_key,
        sample.legacy_recording_key = row.recording_key,
        sample.legacy_segment_id = row.legacy_segment_id,
        sample.speaker_key = row.speaker_key,
        sample.speaker_label = row.speaker_label,
        sample.seed_confidence = row.seed_confidence,
        sample.source_media = row.source_media,
        sample.source_media_resolved = row.source_media_resolved,
        sample.path = row.path,
        sample.container_path = row.container_path,
        sample.start_seconds = row.start,
        sample.end_seconds = row.end,
        sample.duration_seconds = row.end - row.start,
        sample.text = row.text,
        sample.status = row.status,
        sample.error = row.error,
        sample.updated_at = datetime()
    MERGE (identity)-[:HAS_TRAINING_SAMPLE]->(sample)
    """
    driver = driver_from_config(config)
    with driver.session(database=target_db(config)) as session:
        session.run(query, rows=rows, ns=ns).consume()
    driver.close()


def export_training_clips(config: Dict[str, Any], identity: str, limit: int, manifest: str | None = None) -> Dict[str, Any]:
    rows = _legacy_training_candidate_rows(config, identity, limit)
    out_root = Path(container_training_root(config)) / identity
    out_root.mkdir(parents=True, exist_ok=True)
    manifest_path = Path(manifest) if manifest else out_root / "manifest.jsonl"
    exported: List[Dict[str, Any]] = []
    audio_cache: Dict[str, tuple[np.ndarray, int]] = {}
    for row in rows:
        sample_id = sample_id_for(identity, str(row["legacy_segment_id"]))
        container_path = str(out_root / f"{sample_id}.wav")
        enriched = {
            **row,
            "sample_id": sample_id,
            "path": host_sample_path(config, container_path),
            "container_path": container_path,
            "status": "pending",
            "error": "",
        }
        source_path = row.get("source_media_resolved")
        try:
            if not source_path or not Path(source_path).exists():
                raise FileNotFoundError(source_path or row.get("source_media") or "missing source_media")
            if source_path not in audio_cache:
                audio_cache[source_path] = read_wav(source_path)
            audio, sr = audio_cache[source_path]
            start = max(0, int(float(row["start"]) * sr))
            end = min(len(audio), int(float(row["end"]) * sr))
            if end <= start:
                raise ValueError("segment has no audio samples after clipping")
            write_wav(Path(container_path), audio[start:end], sr)
            enriched["status"] = "exported"
        except Exception as exc:
            enriched["status"] = "error"
            enriched["error"] = str(exc)
        exported.append(enriched)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8") as handle:
        for row in exported:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    _register_training_samples(config, exported)
    return {
        "identity": identity,
        "candidates": len(rows),
        "exported": sum(1 for row in exported if row["status"] == "exported"),
        "errors": sum(1 for row in exported if row["status"] == "error"),
        "manifest": str(manifest_path),
        "database": target_db(config),
    }


def build_voiceprint(
    config: Dict[str, Any],
    identity: str,
    manifest: str | None = None,
    limit: int = 200,
    allow_fallback: bool = False,
) -> Dict[str, Any]:
    if manifest:
        with Path(manifest).open("r", encoding="utf-8") as handle:
            samples = [json.loads(line) for line in handle if line.strip()]
    else:
        query = """
        MATCH (:VoiceIdentity {identity_id: $identity})-[:HAS_TRAINING_SAMPLE]->(sample:VoiceTrainingSample)
        WHERE sample.status = 'exported' AND coalesce(sample.container_path, sample.path) IS NOT NULL
        RETURN sample.sample_id AS sample_id,
               coalesce(sample.container_path, sample.path) AS path,
               sample.text AS text
        ORDER BY sample.updated_at DESC
        LIMIT $limit
        """
        driver = driver_from_config(config)
        with driver.session(database=target_db(config)) as session:
            samples = [dict(record) for record in session.run(query, identity=identity, limit=limit)]
        driver.close()
    embedder = SpeakerEmbedder()
    if embedder.model is None and not allow_fallback:
        raise RuntimeError(
            "Real speaker embedding model is not installed. Install the insight extra "
            "or rerun with --allow-fallback to create a weak smoke-test vector."
        )
    embeddings = []
    used = []
    errors = []
    for sample in samples:
        path = sample.get("container_path") or sample.get("path")
        if not path or not Path(path).exists():
            errors.append({"sample_id": sample.get("sample_id"), "error": f"missing file: {path}"})
            continue
        try:
            audio, sr = read_wav(path)
            embeddings.append(embedder.embed(audio, sr))
            used.append({"sample_id": sample.get("sample_id"), "path": path})
        except Exception as exc:
            errors.append({"sample_id": sample.get("sample_id"), "error": str(exc)})
    if not embeddings:
        raise RuntimeError("No usable training samples were available to build a voiceprint")
    matrix = np.array(embeddings, dtype=float)
    embedding = np.mean(matrix, axis=0).tolist()
    voiceprint_id = f"{identity}:{namespace(config)}:current"
    query = """
    MERGE (identity:VoiceIdentity {identity_id: $identity})
      ON CREATE SET identity.created_at = datetime()
    SET identity.name = $identity,
        identity.updated_at = datetime()
    MERGE (voiceprint:Voiceprint {voiceprint_id: $voiceprint_id})
      ON CREATE SET voiceprint.created_at = datetime()
    SET voiceprint.identity_id = $identity,
        voiceprint.embedding = $embedding,
        voiceprint.embedding_dim = size($embedding),
        voiceprint.sample_count = $sample_count,
        voiceprint.model = $model,
        voiceprint.source = 'legacy_training_samples',
        voiceprint.updated_at = datetime()
    MERGE (identity)-[:HAS_VOICEPRINT]->(voiceprint)
    """
    model_name = (
        config.get("voice_insight", {})
        .get("models", {})
        .get("speaker_embedding", "speechbrain/spkrec-ecapa-voxceleb")
    )
    driver = driver_from_config(config)
    with driver.session(database=target_db(config)) as session:
        session.run(
            query,
            identity=identity,
            voiceprint_id=voiceprint_id,
            embedding=embedding,
            sample_count=len(used),
            model=model_name,
        ).consume()
    driver.close()
    return {
        "identity": identity,
        "voiceprint_id": voiceprint_id,
        "sample_count": len(used),
        "embedding_dim": len(embedding),
        "errors": errors[:10],
        "fallback_embedding": embedder.model is None,
        "database": target_db(config),
    }


def _chunks(rows: Iterable[Dict[str, Any]], size: int) -> Iterable[List[Dict[str, Any]]]:
    chunk: List[Dict[str, Any]] = []
    for row in rows:
        chunk.append(row)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _legacy_segment_rows(config: Dict[str, Any], seeded_only: bool, limit: int) -> List[Dict[str, Any]]:
    seed_filter = """
    WITH tx, seg, sp, coalesce(sp.label, seg.speaker_label, 'UNKNOWN') AS label
    WITH tx, seg, sp, label, 'legacy:' + coalesce(sp.key, tx.key) + ':' + label AS cluster_id
    """
    where = ""
    if seeded_only:
        where = "WHERE cluster_id IN $cluster_ids"
    query = f"""
    MATCH (sp:Speaker)<-[:SPOKEN_BY]-(seg:Segment)<-[:HAS_SEGMENT]-(tx:Transcription)
    {seed_filter}
    {where}
    RETURN tx.key AS recording_key,
           tx.source_media AS source_media,
           tx.source_media_prev AS source_media_prev,
           sp.key AS speaker_key,
           label AS speaker_label,
           cluster_id AS cluster_id,
           seg.id AS legacy_segment_id,
           seg.idx AS idx,
           seg.start AS start,
           seg.end AS end,
           seg.text AS text,
           seg.is_lyrics AS is_lyrics,
           seg.music_overlap AS music_overlap,
           seg.lyrics_score AS lyrics_score,
           seg.review_needed AS review_needed
    ORDER BY tx.key, seg.start
    LIMIT $limit
    """
    cluster_ids = []
    if seeded_only:
        for seed in config.get("voice_insight", {}).get("legacy", {}).get("speaker_seeds", []) or []:
            cluster_ids.append(f"legacy:{seed.get('key')}:{seed.get('label')}")
    driver = driver_from_config(config)
    with driver.session(database=source_db(config)) as session:
        rows = [dict(record) for record in session.run(query, limit=limit, cluster_ids=cluster_ids)]
    driver.close()
    return rows


def promote_legacy_segments(config: Dict[str, Any], seeded_only: bool, limit: int, batch_size: int) -> int:
    rows = _legacy_segment_rows(config, seeded_only=seeded_only, limit=limit)
    ns = namespace(config)
    query = """
    UNWIND $rows AS row
    MERGE (recording:VoiceRecording {recording_id: $ns + ':legacy:' + row.recording_key})
      ON CREATE SET recording.created_at = datetime()
    SET recording.source = 'legacy_neo4j',
        recording.legacy_key = row.recording_key,
        recording.source_media = row.source_media,
        recording.source_media_prev = row.source_media_prev,
        recording.updated_at = datetime()
    MERGE (cluster:VoiceSpeakerCluster {cluster_id: row.cluster_id})
      ON CREATE SET cluster.created_at = datetime()
    SET cluster.source = 'legacy_neo4j',
        cluster.legacy_key = row.speaker_key,
        cluster.legacy_label = row.speaker_label,
        cluster.updated_at = datetime()
    MERGE (segment:VoiceSegment {segment_id: $ns + ':legacy-segment:' + row.legacy_segment_id})
      ON CREATE SET segment.created_at = datetime()
    SET segment.recording_id = recording.recording_id,
        segment.cluster_id = cluster.cluster_id,
        segment.legacy_segment_id = row.legacy_segment_id,
        segment.start_seconds = row.start,
        segment.end_seconds = row.end,
        segment.start_ms = toInteger(round(1000.0 * coalesce(row.start, 0.0))),
        segment.end_ms = toInteger(round(1000.0 * coalesce(row.end, 0.0))),
        segment.speaker_label = row.speaker_label,
        segment.is_lyrics = row.is_lyrics,
        segment.music_overlap = row.music_overlap,
        segment.lyrics_score = row.lyrics_score,
        segment.review_needed = row.review_needed,
        segment.source = 'legacy_neo4j',
        segment.updated_at = datetime()
    MERGE (recording)-[:HAS_VOICE_SEGMENT]->(segment)
    MERGE (segment)-[:DIARIZED_AS]->(cluster)
    MERGE (utterance:VoiceUtterance {utterance_id: $ns + ':legacy-utterance:' + row.legacy_segment_id})
      ON CREATE SET utterance.created_at = datetime()
    SET utterance.text = row.text,
        utterance.recording_id = recording.recording_id,
        utterance.segment_id = segment.segment_id,
        utterance.cluster_id = cluster.cluster_id,
        utterance.start_ms = segment.start_ms,
        utterance.end_ms = segment.end_ms,
        utterance.source = 'legacy_neo4j',
        utterance.updated_at = datetime()
    MERGE (segment)-[:HAS_UTTERANCE]->(utterance)
    WITH cluster, segment, utterance
    OPTIONAL MATCH (cluster)-[idrel:IDENTIFIES_AS]->(identity:VoiceIdentity)
    FOREACH (_ IN CASE WHEN identity IS NULL THEN [] ELSE [1] END |
      SET segment.identity_id = identity.identity_id,
          segment.identity_confidence = idrel.confidence,
          segment.identity_source = idrel.source,
          utterance.identity_id = identity.identity_id,
          utterance.identity_confidence = idrel.confidence,
          utterance.identity_source = idrel.source
      MERGE (segment)-[:ATTRIBUTED_TO]->(identity)
      MERGE (utterance)-[:SPOKEN_BY]->(identity)
    )
    RETURN count(*) AS promoted
    """
    total = 0
    driver = driver_from_config(config)
    with driver.session(database=target_db(config)) as session:
        for chunk in _chunks(rows, batch_size):
            record = session.run(query, rows=chunk, ns=ns).single()
            total += record["promoted"] if record else 0
    driver.close()
    return total


def apply_configured_seeds(config: Dict[str, Any]) -> int:
    seeds = config.get("voice_insight", {}).get("legacy", {}).get("speaker_seeds", []) or []
    for seed in seeds:
        seed_legacy_speaker(
            config,
            identity=seed["identity"],
            speaker_key=seed["key"],
            label=seed["label"],
            confidence=float(seed.get("confidence", 0.95)),
        )
    return len(seeds)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sophia offline voice insight graph tools.")
    parser.add_argument("--config", default="configs/voice_insight.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-schema")

    report = sub.add_parser("legacy-speaker-report")
    report.add_argument("--limit", type=int, default=50)
    report.add_argument("--recording-key")
    report.add_argument("--json", action="store_true")

    seed = sub.add_parser("seed-legacy-speaker")
    seed.add_argument("--identity", required=True)
    seed.add_argument("--key", required=True)
    seed.add_argument("--label", required=True)
    seed.add_argument("--confidence", type=float, default=0.95)

    sub.add_parser("apply-configured-seeds")

    promote = sub.add_parser("promote-legacy-segments")
    promote.add_argument("--all", action="store_true", help="Promote all legacy diarized segments, not only seeded clusters.")
    promote.add_argument("--limit", type=int, default=1000)
    promote.add_argument("--batch-size", type=int, default=500)

    candidates = sub.add_parser("training-candidates")
    candidates.add_argument("--identity", default=None)
    candidates.add_argument("--limit", type=int, default=25)
    candidates.add_argument("--json", action="store_true")

    export = sub.add_parser("export-training-clips")
    export.add_argument("--identity", default=None)
    export.add_argument("--limit", type=int, default=None)
    export.add_argument("--manifest")

    voiceprint = sub.add_parser("build-voiceprint")
    voiceprint.add_argument("--identity", default=None)
    voiceprint.add_argument("--manifest")
    voiceprint.add_argument("--limit", type=int, default=200)
    voiceprint.add_argument("--allow-fallback", action="store_true")

    args = parser.parse_args()
    config = load_config(args.config)

    if args.command == "init-schema":
        init_schema(config)
        print(json.dumps({"ok": True, "database": target_db(config)}))
    elif args.command == "legacy-speaker-report":
        rows = legacy_speaker_report(config, args.limit, recording_key=args.recording_key)
        if args.json:
            print(json.dumps(rows, indent=2, ensure_ascii=False))
        else:
            for row in rows:
                samples = " | ".join(str(s).strip().replace("\n", " ") for s in row.get("samples", []) if s)
                print(
                    f"{row.get('speaker_key')} {row.get('label')} {row.get('recording_key')} "
                    f"segments={row.get('segments')} seconds={row.get('seconds')} samples={samples[:220]}"
                )
    elif args.command == "seed-legacy-speaker":
        seed_legacy_speaker(config, args.identity, args.key, args.label, args.confidence)
        print(json.dumps({"seeded": 1, "identity": args.identity, "key": args.key, "label": args.label}))
    elif args.command == "apply-configured-seeds":
        print(json.dumps({"seeded": apply_configured_seeds(config)}))
    elif args.command == "promote-legacy-segments":
        promoted = promote_legacy_segments(config, seeded_only=not args.all, limit=args.limit, batch_size=args.batch_size)
        print(json.dumps({"promoted": promoted, "database": target_db(config)}))
    elif args.command == "training-candidates":
        identity = args.identity or config.get("voice_insight", {}).get("default_identity", "scott")
        rows = collect_training_candidates(config, identity, args.limit)
        if args.json:
            print(json.dumps(rows, indent=2, ensure_ascii=False))
        else:
            for row in rows:
                print(
                    f"{row.get('identity_id')} {row.get('recording_key')} {row.get('speaker_label')} "
                    f"{row.get('start')}-{row.get('end')} source={row.get('source_media_resolved')} "
                    f"text={str(row.get('text') or '').strip()[:160]}"
                )
    elif args.command == "export-training-clips":
        identity = args.identity or config.get("voice_insight", {}).get("default_identity", "scott")
        limit = args.limit or int(training_config(config).get("max_samples_per_run", 100))
        print(json.dumps(export_training_clips(config, identity, limit, manifest=args.manifest), indent=2))
    elif args.command == "build-voiceprint":
        identity = args.identity or config.get("voice_insight", {}).get("default_identity", "scott")
        print(
            json.dumps(
                build_voiceprint(
                    config,
                    identity,
                    manifest=args.manifest,
                    limit=args.limit,
                    allow_fallback=args.allow_fallback,
                ),
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
