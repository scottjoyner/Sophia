from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from ..config import Neo4jConfig


@dataclass(slots=True)
class VoiceprintGraphRecord:
    user_id: str
    group_key: str
    scope: str
    device_id: str | None
    version_id: str
    embedding: List[float]
    samples: Dict[str, Any]
    threshold: float
    sample_count: int
    source: str
    append: bool
    lineage_mode: str
    active: bool
    created_at: str | None = None


class VoiceprintGraphStore:
    def __init__(self, uri: str, user: str, password: str, database: str | None = None):
        self.uri = uri
        self.user = user
        self.password = password
        self.database = database

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

    @staticmethod
    def _parse_samples_json(samples_json: str | None) -> Dict[str, Any]:
        if not samples_json:
            return {}
        try:
            data = json.loads(samples_json)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _record_from_row(row: Dict[str, Any]) -> VoiceprintGraphRecord:
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

    def _query_all(self, query: str, **params: Any) -> List[Dict[str, Any]]:
        driver = self._driver()
        try:
            with driver.session(database=self.database) as session:
                return [dict(row) for row in session.run(query, **params)]
        finally:
            driver.close()

    def save_voiceprint(
        self,
        *,
        user_id: str,
        embedding_mean: Iterable[float],
        samples: Dict[str, Any],
        threshold: float,
        source: str,
        append: bool,
        device_id: str | None = None,
        capture_id: str | None = None,
        session_id: str | None = None,
    ) -> Dict[str, Any]:
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
                        UNWIND $samples AS sample
                        CREATE (voice_sample:VoiceprintSample {
                            sample_id: sample.sample_id,
                            user_id: $user_id,
                            group_key: $group_key,
                            device_id: $device_id,
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
                        """,
                        user_id=user_id,
                        group_key=group_key,
                        device_id=device_id or "",
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
        return record

    def get_active_records(self, user_id: str) -> List[Dict[str, Any]]:
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
        return records

    def get_active_record(self, user_id: str) -> Dict[str, Any] | None:
        records = self.get_active_records(user_id)
        for record in records:
            if record.get("scope") == "identity":
                return record
        return records[0] if records else None

    def get_device_record(self, user_id: str, device_id: str) -> Dict[str, Any] | None:
        for record in self.get_active_records(user_id):
            if record.get("scope") == "device" and (record.get("device_id") or "") == device_id:
                return record
        return None

    def list_user_ids(self) -> List[str]:
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
