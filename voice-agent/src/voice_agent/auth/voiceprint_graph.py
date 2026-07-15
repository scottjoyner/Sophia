from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from ..config import Neo4jConfig


@dataclass(slots=True)
class VoiceprintGraphRecord:
    user_id: str
    group_key: str
    scope: str
    device_id: str | None
    version_id: str
    embedding: list[float]
    samples: dict[str, Any]
    threshold: float
    sample_count: int
    source: str
    append: bool
    lineage_mode: str
    active: bool
    created_at: str | None = None


def _neo4j_json(value: object) -> object:
    """Make Neo4j temporal types JSON-serializable for FastAPI responses."""
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value
    try:
        from neo4j.time import Date, DateTime, Time

        if isinstance(value, (DateTime, Date, Time)):
            return value.isoformat()
    except Exception:
        pass
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return value


def link_global_speaker_by_label(session, user_id: str, embedding: Sequence[float]) -> dict[str, Any] | None:
    """Bridge a VoiceIdentity to a global ``GlobalSpeaker`` node whose display label
    matches the owner's user_id (case-insensitive). This links the trained owner
    voiceprint embedding onto the canonical global speaker so diarization/linkage
    across the fleet can resolve to the same persona.

    Returns the linkage record, or ``None`` when no matching global speaker exists.
    """
    try:
        row = session.run(
            """
            MATCH (gs:GlobalSpeaker)
            WHERE toLower(gs.display_label) = toLower($user_id)
            RETURN gs.id AS id, gs.display_label AS display_label
            LIMIT 1
            """,
            user_id=user_id,
        ).single()
        if not row:
            return None
        global_id = row["id"]
        global_label = row.get("display_label") or user_id
        session.run(
            """
            MERGE (identity:VoiceIdentity {user_id: $user_id})
            MERGE (gs:GlobalSpeaker {id: $global_id})
              ON CREATE SET gs.display_label = $display_label, gs.created_at = datetime()
            SET gs.embedding = $embedding, gs.is_owner_voiceprint = true, gs.updated_at = datetime()
            MERGE (identity)-[r:IS_GLOBAL_SPEAKER]->(gs)
            SET r.score = 1.0, r.method = 'label_match', r.linked_at = datetime()
            """,
            user_id=user_id,
            global_id=global_id,
            display_label=global_label,
            embedding=list(embedding),
        )
        return {
            "global_speaker_id": global_id,
            "display_label": global_label,
            "method": "label_match",
        }
    except Exception:
        return None


class VoiceprintGraphStore:
    EMBEDDING_DIMENSION: ClassVar[int] = 192
    _schema_lock: ClassVar[threading.Lock] = threading.Lock()
    _schema_bootstrapped: ClassVar[set[tuple[str, str, str | None]]] = set()

    def __init__(self, uri: str, user: str, password: str, database: str | None = None):
        self.uri = uri
        self.user = user
        self.password = password
        self.database = self._resolve_database(uri, user, password, database)
        self.schema_error: str | None = None
        self.ensure_schema()

    @staticmethod
    def _resolve_database(uri: str, user: str, password: str, database: str | None) -> str | None:
        """Pick a database that actually exists.

        The configured database (often the legacy default ``memory``) may not exist
        on a given deployment. Fall back to ``neo4j`` or the first non-system user
        database so the voiceprint graph works without manual tuning.
        """
        try:
            from neo4j import GraphDatabase

            driver = GraphDatabase.driver(uri, auth=(user, password))
            try:
                with driver.session() as session:
                    names = [row["name"] for row in session.run("SHOW DATABASES YIELD name")]
                if database in names:
                    return database
                if "neo4j" in names:
                    return "neo4j"
                user_dbs = [name for name in names if name not in {"system"}]
                if user_dbs:
                    return user_dbs[0]
            finally:
                driver.close()
        except Exception:
            pass
        return database

    @classmethod
    def from_config(cls, config: Neo4jConfig | None) -> VoiceprintGraphStore | None:
        if not config or not config.password:
            return None
        return cls(config.uri, config.user, config.password, config.database)

    @staticmethod
    def _group_key(user_id: str, device_id: str | None) -> str:
        if device_id:
            return f"{user_id}:device:{device_id}"
        return f"{user_id}:identity"

    @staticmethod
    def _scope(device_id: str | None) -> str:
        return "device" if device_id else "identity"

    def _driver(self):
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("neo4j driver not installed") from exc
        return GraphDatabase.driver(self.uri, auth=(self.user, self.password))

    def ensure_schema(self) -> None:
        bootstrap_key = (self.uri, self.user, self.database)
        if bootstrap_key in self._schema_bootstrapped:
            return
        with self._schema_lock:
            if bootstrap_key in self._schema_bootstrapped:
                return
            queries = [
                (
                    "voiceprint_version_embedding_idx",
                    f"""
                    CREATE VECTOR INDEX voiceprint_version_embedding_idx IF NOT EXISTS
                    FOR (n:VoiceprintVersion) ON (n.embedding)
                    OPTIONS {{indexConfig: {{
                        `vector.dimensions`: {self.EMBEDDING_DIMENSION},
                        `vector.similarity_function`: 'COSINE'
                    }}}}
                    """,
                ),
                (
                    "speaker_embedding_idx",
                    f"""
                    CREATE VECTOR INDEX speaker_embedding_idx IF NOT EXISTS
                    FOR (n:Speaker) ON (n.embedding)
                    OPTIONS {{indexConfig: {{
                        `vector.dimensions`: {self.EMBEDDING_DIMENSION},
                        `vector.similarity_function`: 'COSINE'
                    }}}}
                    """,
                ),
            ]
            driver = None
            try:
                driver = self._driver()
                with driver.session(database=self.database) as session:
                    for _, query in queries:
                        session.run(query).consume()
                self._schema_bootstrapped.add(bootstrap_key)
                self.schema_error = None
            except Exception as exc:  # pragma: no cover - bootstrap should not block the app
                self.schema_error = f"{type(exc).__name__}: {exc}"
            finally:
                if driver is not None:
                    driver.close()

    @staticmethod
    def _parse_samples_json(samples_json: str | None) -> dict[str, Any]:
        if not samples_json:
            return {}
        try:
            data = json.loads(samples_json)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _record_from_row(row: dict[str, Any]) -> VoiceprintGraphRecord:
        samples = VoiceprintGraphStore._parse_samples_json(row.get("samples_json"))
        return VoiceprintGraphRecord(
            user_id=row["user_id"],
            group_key=row["group_key"],
            scope=row["scope"],
            device_id=row.get("device_id") or None,
            version_id=row["version_id"],
            embedding=list(row.get("embedding") or []),
            samples=samples,
            threshold=float(row.get("threshold") or 0.0),
            sample_count=int(row.get("sample_count") or len(samples.get("samples") or [])),
            source=str(row.get("source") or ""),
            append=bool(row.get("append")),
            lineage_mode=str(row.get("lineage_mode") or "initial"),
            active=bool(row.get("active", True)),
            created_at=str(row.get("created_at") or ""),
        )

    def _query_single(self, query: str, **params: Any):
        driver = self._driver()
        try:
            with driver.session(database=self.database) as session:
                return session.run(query, **params).single()
        finally:
            driver.close()

    def _query_all(self, query: str, **params: Any) -> list[dict[str, Any]]:
        driver = self._driver()
        try:
            with driver.session(database=self.database) as session:
                return [dict(row) for row in session.run(query, **params)]
        finally:
            driver.close()

    @staticmethod
    def _candidate_id_from_row(row: dict[str, Any]) -> str:
        candidate_type = row.get("candidate_type") or "version"
        if candidate_type == "sample":
            sample_id = row.get("sample_id") or row.get("sample_sha256") or row.get("version_id")
            return f"sample:{row.get('version_id')}:{sample_id}"
        return f"version:{row.get('version_id')}"

    def _search_vector_candidates(
        self,
        *,
        user_id: str,
        embedding: Sequence[float],
        top_k: int,
    ) -> list[dict[str, Any]]:
        version_query = """
        CALL db.index.vector.queryNodes('voiceprint_version_embedding_idx', $limit, $embedding) YIELD node, score
        WITH node, score
        WHERE node.user_id = $user_id
        RETURN
            node.user_id AS user_id,
            node.group_key AS group_key,
            node.scope AS scope,
            node.device_id AS device_id,
            node.version_id AS version_id,
            node.version_id AS candidate_id_raw,
            'version' AS candidate_type,
            NULL AS sample_id,
            NULL AS sample_sha256,
            NULL AS sample_path,
            NULL AS sample_source,
            node.embedding AS embedding,
            node.threshold AS threshold,
            node.sample_count AS sample_count,
            node.source AS source,
            node.append AS append,
            node.lineage_mode AS lineage_mode,
            coalesce(node.active, true) AS active,
            node.created_at AS created_at,
            score AS score
        ORDER BY score DESC
        LIMIT $limit
        """
        sample_query = """
        CALL db.index.vector.queryNodes('voiceprint_sample_embedding_idx', $limit, $embedding) YIELD node, score
        WITH node, score
        WHERE node.user_id = $user_id
        RETURN
            node.user_id AS user_id,
            node.group_key AS group_key,
            CASE WHEN coalesce(node.device_id, '') = '' THEN 'identity' ELSE 'device' END AS scope,
            node.device_id AS device_id,
            node.version_id AS version_id,
            coalesce(node.sample_id, node.version_id) AS sample_id,
            'sample' AS candidate_type,
            coalesce(node.sha256, '') AS sample_sha256,
            coalesce(node.path, '') AS sample_path,
            coalesce(node.source, '') AS sample_source,
            node.embedding AS embedding,
            NULL AS threshold,
            NULL AS sample_count,
            coalesce(node.source, '') AS source,
            NULL AS append,
            NULL AS lineage_mode,
            true AS active,
            node.created_at AS created_at,
            score AS score
        ORDER BY score DESC
        LIMIT $limit
        """
        results: list[dict[str, Any]] = []
        driver = self._driver()
        try:
            with driver.session(database=self.database) as session:
                version_rows = [dict(row) for row in session.run(version_query, user_id=user_id, embedding=list(embedding), limit=top_k)]
                sample_rows = [dict(row) for row in session.run(sample_query, user_id=user_id, embedding=list(embedding), limit=top_k)]
        finally:
            driver.close()
        combined: dict[str, dict[str, Any]] = {}
        for row in version_rows + sample_rows:
            candidate_id = self._candidate_id_from_row(row)
            row["candidate_id"] = candidate_id
            if candidate_id not in combined or float(row.get("score") or 0.0) > float(combined[candidate_id].get("score") or 0.0):
                combined[candidate_id] = row
        results = sorted(combined.values(), key=lambda row: float(row.get("score") or 0.0), reverse=True)
        return results[:top_k]

    def save_voiceprint(
        self,
        *,
        user_id: str,
        embedding_mean: Iterable[float],
        samples: dict[str, Any],
        threshold: float,
        source: str,
        append: bool,
        device_id: str | None = None,
        capture_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        group_key = self._group_key(user_id, device_id)
        scope = self._scope(device_id)
        version_id = uuid.uuid4().hex
        sample_rows = list(samples.get("samples") or [])
        active_query = """
        MATCH (g:VoiceprintGroup {group_key: $group_key})
        OPTIONAL MATCH (g)-[:ACTIVE_VERSION]->(v:VoiceprintVersion)
        RETURN v.version_id AS version_id
        LIMIT 1
        """
        current = self._query_single(active_query, group_key=group_key)
        current_version_id = current["version_id"] if current else None
        lineage_mode = "initial"
        if current_version_id:
            lineage_mode = "append" if append else "replace"

        driver = self._driver()
        try:
            with driver.session(database=self.database) as session:
                session.run(
                    """
                    MERGE (identity:VoiceIdentity {user_id: $user_id})
                      ON CREATE SET identity.created_at = datetime()
                    MERGE (group:VoiceprintGroup {group_key: $group_key})
                      ON CREATE SET group.created_at = datetime()
                    SET group.user_id = $user_id,
                        group.scope = $scope,
                        group.device_id = $device_id,
                        group.label = $group_label,
                        group.source = $source,
                        group.updated_at = datetime(),
                        group.deleted = false,
                        group.deleted_at = NULL,
                        group.last_source = $source,
                        group.last_sample_count = $sample_count,
                        group.current_version_id = $version_id
                    MERGE (identity)-[:HAS_GROUP]->(group)
                    """,
                    user_id=user_id,
                    group_key=group_key,
                    scope=scope,
                    device_id=device_id or "",
                    group_label=device_id or user_id,
                    source=source,
                    sample_count=len(sample_rows),
                    version_id=version_id,
                )
                if sample_rows:
                    session.run(
                        """
                        MATCH (group:VoiceprintGroup {group_key: $group_key})
                        MATCH (version:VoiceprintVersion {version_id: $version_id})
                        UNWIND $samples AS sample
                        CREATE (voice_sample:VoiceprintSample {
                            sample_id: sample.sample_id,
                            user_id: $user_id,
                            group_key: $group_key,
                            device_id: $device_id,
                            version_id: $version_id,
                            sha256: sample.sha256,
                            path: sample.path,
                            source: sample.source,
                            sample_rate: sample.sample_rate,
                            duration_seconds: sample.duration_seconds,
                            energy: sample.energy,
                            embedding: sample.embedding,
                            added_ts_ms: sample.added_ts_ms,
                            created_at: datetime()
                        })
                        MERGE (group)-[:HAS_SAMPLE]->(voice_sample)
                        MERGE (version)-[:HAS_SAMPLE]->(voice_sample)
                        """,
                        user_id=user_id,
                        group_key=group_key,
                        device_id=device_id or "",
                        version_id=version_id,
                        samples=[
                            {
                                "sample_id": uuid.uuid4().hex,
                                "sha256": sample.get("sha256", ""),
                                "path": sample.get("path", ""),
                                "source": sample.get("source", source),
                                "sample_rate": sample.get("sample_rate"),
                                "duration_seconds": sample.get("duration_seconds"),
                                "energy": sample.get("energy"),
                                "embedding": sample.get("embedding"),
                                "added_ts_ms": sample.get("added_ts_ms"),
                            }
                            for sample in sample_rows
                        ],
                    )
                session.run(
                    """
                    MATCH (group:VoiceprintGroup {group_key: $group_key})
                    CREATE (version:VoiceprintVersion {
                        version_id: $version_id
                    })
                    CREATE (group)-[:HAS_VERSION]->(version)
                    SET version.active = true
                    """,
                    group_key=group_key,
                    version_id=version_id,
                )
                session.run(
                    """
                    MATCH (group:VoiceprintGroup {group_key: $group_key})
                    MATCH (version:VoiceprintVersion {version_id: $version_id})
                    SET version.user_id = $user_id,
                        version.group_key = $group_key,
                        version.scope = $scope,
                        version.device_id = $device_id,
                        version.embedding = $embedding,
                        version.threshold = $threshold,
                        version.sample_count = $sample_count,
                        version.samples_json = $samples_json,
                        version.append = $append,
                        version.lineage_mode = $lineage_mode,
                        version.source = $source,
                        version.capture_id = $capture_id,
                        version.session_id = $session_id,
                        version.created_at = datetime(),
                        version.active = true
                    """,
                    group_key=group_key,
                    version_id=version_id,
                    user_id=user_id,
                    scope=scope,
                    device_id=device_id or "",
                    embedding=list(embedding_mean),
                    threshold=threshold,
                    sample_count=len(sample_rows),
                    samples_json=json.dumps(samples, ensure_ascii=False),
                    append=append,
                    lineage_mode=lineage_mode,
                    source=source,
                    capture_id=capture_id or "",
                    session_id=session_id or "",
                )
                if current_version_id:
                    session.run(
                        """
                        MATCH (group:VoiceprintGroup {group_key: $group_key})-[rel:ACTIVE_VERSION]->(prev:VoiceprintVersion {version_id: $prev_version_id})
                        DELETE rel
                        WITH prev
                        MATCH (version:VoiceprintVersion {version_id: $version_id})
                        MERGE (version)-[:DERIVED_FROM]->(prev)
                        SET prev.active = false,
                            prev.superseded_at = datetime()
                        """,
                        group_key=group_key,
                        prev_version_id=current_version_id,
                        version_id=version_id,
                    )
                session.run(
                    """
                    MATCH (group:VoiceprintGroup {group_key: $group_key})
                    MATCH (version:VoiceprintVersion {version_id: $version_id})
                    MERGE (group)-[:ACTIVE_VERSION]->(version)
                    SET group.active_version_id = $version_id,
                        group.updated_at = datetime(),
                        version.active = true
                    """,
                    group_key=group_key,
                    version_id=version_id,
                )
        finally:
            driver.close()

        record = {
            "user_id": user_id,
            "group_key": group_key,
            "scope": scope,
            "device_id": device_id,
            "version_id": version_id,
            "embedding": list(embedding_mean),
            "samples": samples,
            "threshold": threshold,
            "sample_count": len(sample_rows),
            "source": source,
            "append": append,
            "lineage_mode": lineage_mode,
            "active": True,
            "captured_in_graph": True,
        }
        if device_id is None:
            try:
                record["speaker_linkage"] = self.link_identity_to_global_speakers(
                    user_id, embedding_mean, match_threshold=threshold
                )
            except Exception as exc:
                record["speaker_linkage"] = {"linked": False, "error": f"{type(exc).__name__}: {exc}"}
        return record

    def link_identity_to_global_speakers(
        self,
        user_id: str,
        embedding: Sequence[float],
        *,
        match_threshold: float = 0.85,
    ) -> dict[str, Any]:
        """Resolve the owner's trained embedding into the global Speaker pool.

        If a global Speaker already carries an embedding that is a strong match
        (>= match_threshold), we cross-link the VoiceIdentity to that Speaker.
        Otherwise we ensure the owner has their own Speaker node (seeded with the
        embedding) and link to it. Either way the trained embedding becomes part
        of the global speaker graph so future diarization/linkage can find it.
        """
        import os

        if os.getenv("SOPHIA_GLOBAL_SPEAKER_LINK_ENABLED", "true").lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            return {"linked": False, "enabled": False, "reason": "disabled"}
        try:
            match_threshold = float(os.getenv("SOPHIA_GLOBAL_SPEAKER_LINK_THRESHOLD", str(match_threshold)))
        except (TypeError, ValueError):
            pass

        driver = self._driver()
        try:
            with driver.session(database=self.database) as session:
                best_row = None
                try:
                    rows = [
                        dict(row)
                        for row in session.run(
                            """
                            CALL db.index.vector.queryNodes('speaker_embedding_idx', 5, $embedding) YIELD node, score
                            WHERE node:Speaker
                            RETURN node.user_id AS speaker_user_id, score AS score
                            ORDER BY score DESC LIMIT 1
                            """,
                            embedding=list(embedding),
                        )
                    ]
                    if rows:
                        best_row = rows[0]
                except Exception:
                    best_row = None

                best_score = float(best_row["score"]) if best_row else 0.0
                if best_row and str(best_row.get("speaker_user_id")) != str(user_id) and best_score >= match_threshold:
                    speaker_user_id = str(best_row["speaker_user_id"])
                    session.run(
                        """
                        MERGE (identity:VoiceIdentity {user_id: $user_id})
                        MERGE (speaker:Speaker {user_id: $speaker_user_id})
                          ON CREATE SET speaker.name = $speaker_user_id, speaker.created_at = datetime()
                        SET speaker.embedding = $embedding, speaker.last_matched_at = datetime()
                        MERGE (identity)-[r:IS_SPEAKER]->(speaker)
                        SET r.score = $score, r.method = 'embedding_match', r.linked_at = datetime()
                        """,
                        user_id=user_id,
                        speaker_user_id=speaker_user_id,
                        embedding=list(embedding),
                        score=best_score,
                    )
                    global_link = link_global_speaker_by_label(session, user_id, embedding)
                    result = {
                        "linked": True,
                        "enabled": True,
                        "created": False,
                        "matched_speaker_user_id": speaker_user_id,
                        "score": best_score,
                        "method": "embedding_match",
                    }
                    if global_link:
                        result["global_speaker"] = global_link
                    return result

                method = "owner_voiceprint_created"
                if best_row and str(best_row.get("speaker_user_id")) == str(user_id):
                    method = "embedding_match_existing_owner"
                session.run(
                    """
                    MERGE (speaker:Speaker {user_id: $user_id})
                      ON CREATE SET speaker.name = $user_id, speaker.created_at = datetime()
                    SET speaker.embedding = $embedding,
                        speaker.is_owner_voiceprint = true,
                        speaker.updated_at = datetime()
                    WITH speaker
                    MERGE (identity:VoiceIdentity {user_id: $user_id})
                    MERGE (identity)-[r:IS_SPEAKER]->(speaker)
                    SET r.score = 1.0, r.method = $method, r.linked_at = datetime()
                    """,
                    user_id=user_id,
                    embedding=list(embedding),
                    method=method,
                )
                global_link = link_global_speaker_by_label(session, user_id, embedding)
                result = {
                    "linked": True,
                    "enabled": True,
                    "created": True,
                    "matched_speaker_user_id": user_id,
                    "score": 1.0,
                    "method": method,
                }
                if global_link:
                    result["global_speaker"] = global_link
                return result
        finally:
            driver.close()

    def get_identity_linkage(self, user_id: str) -> list[dict[str, Any]]:
        query = """
        MATCH (identity:VoiceIdentity {user_id: $user_id})-[r:IS_SPEAKER]->(speaker:Speaker)
        RETURN speaker.user_id AS speaker_user_id,
               speaker.name AS speaker_name,
               coalesce(speaker.is_owner_voiceprint, false) AS is_owner,
               r.score AS score,
               r.method AS method,
               r.linked_at AS linked_at
        ORDER BY r.score DESC
        """
        return [
            {**dict(row), "linked_at": _neo4j_json(row.get("linked_at"))}
            for row in self._query_all(query, user_id=user_id)
        ]

    def backfill_global_speaker_embeddings(self, match_threshold: float | None = None) -> dict[str, Any]:
        """Re-run global-speaker linking for every enrolled identity.

        Use this to (re)populate the ``speaker_embedding_idx`` and bridge owner
        voiceprints to global ``Speaker``/``GlobalSpeaker`` nodes after a fresh
        database, a schema change, or enabling global linkage. Identity-scope
        voiceprints are linked; device-scope voiceprints are folded into the
        identity's speaker node automatically during enroll, so we only need the
        identity embeddings here.
        """
        if match_threshold is None:
            match_threshold = 0.85
        summary: dict[str, Any] = {"users": [], "linked": 0, "skipped": 0, "errors": 0}
        for user_id in self.list_user_ids():
            try:
                record = self.get_active_record(user_id)
                embedding = (record or {}).get("embedding") or []
                if not embedding:
                    summary["skipped"] += 1
                    summary["users"].append({"user_id": user_id, "status": "no_embedding"})
                    continue
                linkage = self.link_identity_to_global_speakers(user_id, embedding, match_threshold=match_threshold)
                if linkage.get("linked"):
                    summary["linked"] += 1
                else:
                    summary["skipped"] += 1
                summary["users"].append({"user_id": user_id, "status": "ok", "linkage": linkage})
            except Exception as exc:
                summary["errors"] += 1
                summary["users"].append({"user_id": user_id, "status": "error", "error": f"{type(exc).__name__}: {exc}"})
        return summary

    def get_active_records(self, user_id: str) -> list[dict[str, Any]]:
        query = """
        MATCH (identity:VoiceIdentity {user_id: $user_id})-[:HAS_GROUP]->(group:VoiceprintGroup)
        WHERE coalesce(group.deleted, false) = false
        OPTIONAL MATCH (group)-[:ACTIVE_VERSION]->(version:VoiceprintVersion)
        WHERE version.version_id IS NOT NULL AND coalesce(version.active, true) = true
        RETURN
            group.group_key AS group_key,
            group.scope AS scope,
            group.device_id AS device_id,
            version.version_id AS version_id,
            version.embedding AS embedding,
            version.samples_json AS samples_json,
            version.threshold AS threshold,
            version.sample_count AS sample_count,
            version.source AS source,
            version.append AS append,
            version.lineage_mode AS lineage_mode,
            version.active AS active,
            version.created_at AS created_at
        ORDER BY CASE WHEN group.scope = 'identity' THEN 0 ELSE 1 END, group.device_id
        """
        records = self._query_all(query, user_id=user_id)
        for record in records:
            record["user_id"] = user_id
            record["created_at"] = _neo4j_json(record.get("created_at"))
        return records

    def get_historical_candidates(self, user_id: str) -> list[dict[str, Any]]:
        query = """
        MATCH (identity:VoiceIdentity {user_id: $user_id})-[:HAS_GROUP]->(group:VoiceprintGroup)
        WHERE coalesce(group.deleted, false) = false
        MATCH (group)-[:HAS_VERSION]->(version:VoiceprintVersion)
        RETURN
            group.group_key AS group_key,
            group.scope AS scope,
            group.device_id AS device_id,
            version.version_id AS version_id,
            version.embedding AS embedding,
            version.samples_json AS samples_json,
            version.threshold AS threshold,
            version.sample_count AS sample_count,
            version.source AS source,
            version.append AS append,
            version.lineage_mode AS lineage_mode,
            version.active AS active,
            version.created_at AS created_at
        ORDER BY CASE WHEN coalesce(version.active, true) = true THEN 0 ELSE 1 END, version.created_at DESC
        """
        records = self._query_all(query, user_id=user_id)
        candidates: list[dict[str, Any]] = []
        for record in records:
            samples = self._parse_samples_json(record.get("samples_json"))
            base = dict(record)
            base["user_id"] = user_id
            base["candidate_id"] = f"version:{record['version_id']}"
            base["candidate_type"] = "version"
            candidates.append(base)
            for idx, sample in enumerate(samples.get("samples") or []):
                embedding = sample.get("embedding")
                if not isinstance(embedding, list) or not embedding:
                    continue
                sample_id = str(sample.get("sample_id") or sample.get("sha256") or f"{record['version_id']}:{idx}")
                candidates.append(
                    {
                        "user_id": user_id,
                        "group_key": record.get("group_key"),
                        "scope": record.get("scope"),
                        "device_id": record.get("device_id") or None,
                        "version_id": record.get("version_id"),
                        "candidate_id": f"sample:{record['version_id']}:{sample_id}",
                        "candidate_type": "sample",
                        "sample_id": sample_id,
                        "sample_sha256": sample.get("sha256"),
                        "sample_path": sample.get("path"),
                        "sample_source": sample.get("source") or record.get("source"),
                        "sample_rate": sample.get("sample_rate"),
                        "duration_seconds": sample.get("duration_seconds"),
                        "energy": sample.get("energy"),
                        "embedding": list(embedding),
                        "threshold": record.get("threshold"),
                        "sample_count": record.get("sample_count"),
                        "source": record.get("source"),
                        "append": record.get("append"),
                        "lineage_mode": record.get("lineage_mode"),
                        "active": bool(record.get("active", True)),
                        "created_at": record.get("created_at"),
                    }
                )
        return candidates

    def search_candidates(self, user_id: str, embedding: Sequence[float], top_k: int = 5) -> list[dict[str, Any]]:
        try:
            results = self._search_vector_candidates(user_id=user_id, embedding=embedding, top_k=top_k)
            if results:
                return results
        except Exception:
            pass
        historical = self.get_historical_candidates(user_id)
        scored: list[dict[str, Any]] = []
        embedding_array = list(embedding)
        if not embedding_array:
            return historical[:top_k]
        for row in historical:
            stored = row.get("embedding") or []
            if len(stored) != len(embedding_array):
                continue
            try:
                import numpy as np

                query = np.array(embedding_array, dtype=float)
                stored_vec = np.array(stored, dtype=float)
                denom = float(np.linalg.norm(query) * np.linalg.norm(stored_vec))
                score = float(np.dot(query, stored_vec) / denom) if denom else 0.0
            except Exception:
                continue
            candidate = dict(row)
            candidate["score"] = score
            candidate["candidate_id"] = candidate.get("candidate_id") or self._candidate_id_from_row(candidate)
            scored.append(candidate)
        scored.sort(key=lambda row: float(row.get("score") or 0.0), reverse=True)
        return scored[:top_k]

    def get_active_record(self, user_id: str) -> dict[str, Any] | None:
        records = self.get_active_records(user_id)
        for record in records:
            if record.get("scope") == "identity":
                return record
        return records[0] if records else None

    def get_device_record(self, user_id: str, device_id: str) -> dict[str, Any] | None:
        for record in self.get_active_records(user_id):
            if record.get("scope") == "device" and (record.get("device_id") or "") == device_id:
                return record
        return None

    def list_user_ids(self) -> list[str]:
        query = """
        MATCH (identity:VoiceIdentity)
        RETURN DISTINCT identity.user_id AS user_id
        ORDER BY identity.user_id
        """
        return [str(row["user_id"]) for row in self._query_all(query) if row.get("user_id")]

    def delete_device_voiceprint(self, user_id: str, device_id: str) -> bool:
        group_key = self._group_key(user_id, device_id)
        query = """
        MATCH (group:VoiceprintGroup {group_key: $group_key, user_id: $user_id})
        OPTIONAL MATCH (group)-[active_rel:ACTIVE_VERSION]->(version:VoiceprintVersion)
        DELETE active_rel
        SET group.deleted = true,
            group.deleted_at = datetime(),
            group.updated_at = datetime()
        WITH group, version
        FOREACH (_ IN CASE WHEN version IS NULL THEN [] ELSE [1] END |
          SET version.active = false,
              version.deleted = true,
              version.deleted_at = datetime()
        )
        RETURN group.group_key AS group_key
        """
        driver = self._driver()
        try:
            with driver.session(database=self.database) as session:
                return session.run(query, group_key=group_key, user_id=user_id).single() is not None
        finally:
            driver.close()
