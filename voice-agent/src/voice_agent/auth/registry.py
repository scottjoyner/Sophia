from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from ..config import AppConfig
from ..util.db import Database
from .voiceprint_graph import VoiceprintGraphStore


def _jsonable(value: object) -> object:
    """Best-effort JSON-safe conversion for values coming from the graph store."""
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return str(value)


class VoiceprintRegistry:
    def __init__(self, db_path: Path, config: AppConfig | None = None):
        self.db = Database(db_path)
        self.graph = VoiceprintGraphStore.from_config(config.neo4j) if config else None

    @staticmethod
    def _sample_count(samples: Dict[str, Any] | None) -> int:
        if not samples:
            return 0
        return len(samples.get("samples") or [])

    @staticmethod
    def _sqlite_record(
        user_id: str,
        embedding_mean: Iterable[float],
        samples: Dict[str, Any],
        threshold: float,
        *,
        device_id: str | None = None,
    ) -> Dict[str, Any]:
        return {
            "user_id": user_id,
            "device_id": device_id,
            "embedding": list(embedding_mean),
            "samples": samples,
            "threshold": threshold,
            "sample_count": VoiceprintRegistry._sample_count(samples),
            "source": samples.get("source") if isinstance(samples, dict) else None,
            "active": True,
        }

    @staticmethod
    def _normalize_graph_record(record: Dict[str, Any]) -> Dict[str, Any]:
        samples = record.get("samples")
        if not isinstance(samples, dict):
            samples_json = record.get("samples_json")
            if isinstance(samples_json, str) and samples_json:
                try:
                    parsed = json.loads(samples_json)
                    samples = parsed if isinstance(parsed, dict) else {}
                except Exception:
                    samples = {}
            else:
                samples = {}
        return {
            "user_id": record.get("user_id"),
            "device_id": record.get("device_id") or None,
            "version_id": record.get("version_id"),
            "group_key": record.get("group_key"),
            "scope": record.get("scope"),
            "embedding": list(record.get("embedding") or []),
            "samples": samples,
            "samples_json": record.get("samples_json"),
            "threshold": record.get("threshold"),
            "sample_count": int(record.get("sample_count") or VoiceprintRegistry._sample_count(samples)),
            "source": record.get("source"),
            "append": bool(record.get("append")),
            "lineage_mode": record.get("lineage_mode"),
            "active": bool(record.get("active", True)),
            "created_at": _jsonable(record.get("created_at")),
        }

    @staticmethod
    def _normalize_candidate_record(record: Dict[str, Any]) -> Dict[str, Any]:
        samples = record.get("samples")
        if not isinstance(samples, dict):
            samples_json = record.get("samples_json")
            if isinstance(samples_json, str) and samples_json:
                try:
                    parsed = json.loads(samples_json)
                    samples = parsed if isinstance(parsed, dict) else {}
                except Exception:
                    samples = {}
            else:
                samples = {}
        normalized = {
            "user_id": record.get("user_id"),
            "group_key": record.get("group_key"),
            "scope": record.get("scope"),
            "device_id": record.get("device_id") or None,
            "version_id": record.get("version_id"),
            "candidate_id": record.get("candidate_id") or (f"version:{record.get('version_id')}" if record.get("version_id") else None),
            "candidate_type": record.get("candidate_type") or "version",
            "sample_id": record.get("sample_id"),
            "sample_sha256": record.get("sample_sha256"),
            "sample_path": record.get("sample_path"),
            "sample_source": record.get("sample_source"),
            "sample_rate": record.get("sample_rate"),
            "duration_seconds": record.get("duration_seconds"),
            "energy": record.get("energy"),
            "embedding": list(record.get("embedding") or []),
            "samples": samples,
            "samples_json": record.get("samples_json"),
            "threshold": record.get("threshold"),
            "sample_count": int(record.get("sample_count") or VoiceprintRegistry._sample_count(samples)),
            "source": record.get("source"),
            "append": bool(record.get("append")),
            "lineage_mode": record.get("lineage_mode"),
            "active": bool(record.get("active", True)),
            "created_at": _jsonable(record.get("created_at")),
        }
        return normalized

    def save(
        self,
        user_id: str,
        embedding_mean: Iterable[float],
        samples: Dict[str, Any],
        threshold: float,
        *,
        source: str = "manual",
        append: bool = False,
        device_id: str | None = None,
        capture_id: str | None = None,
        session_id: str | None = None,
    ) -> Dict[str, Any]:
        graph_result: Dict[str, Any] | None = None
        graph_error: str | None = None
        if self.graph:
            try:
                graph_result = self.graph.save_voiceprint(
                    user_id=user_id,
                    embedding_mean=embedding_mean,
                    samples=samples,
                    threshold=threshold,
                    source=source,
                    append=append,
                    device_id=device_id,
                    capture_id=capture_id,
                    session_id=session_id,
                )
            except Exception as exc:
                graph_result = None
                graph_error = f"{type(exc).__name__}: {exc}"
        if device_id:
            self.db.save_device_voiceprint(user_id, device_id, embedding_mean, samples, threshold)
        else:
            self.db.save_voiceprint(user_id, embedding_mean, samples, threshold)
        record = graph_result or self._sqlite_record(user_id, embedding_mean, samples, threshold, device_id=device_id)
        record["graph_saved"] = bool(graph_result)
        record["graph_error"] = graph_error
        record["graph_enabled"] = bool(self.graph)
        record["speaker_linkage"] = (graph_result or {}).get("speaker_linkage")
        return record

    def get(self, user_id: str) -> Dict[str, Any] | None:
        if self.graph:
            try:
                record = self.graph.get_active_record(user_id)
                if record:
                    return self._normalize_graph_record(record)
            except Exception:
                pass
        return self.db.fetch_voiceprint(user_id)

    def get_device(self, user_id: str, device_id: str) -> Dict[str, Any] | None:
        if self.graph:
            try:
                record = self.graph.get_device_record(user_id, device_id)
                if record:
                    return self._normalize_graph_record(record)
            except Exception:
                pass
        devices = self.get_devices(user_id)
        return devices.get(device_id)

    def save_device(
        self,
        user_id: str,
        device_id: str,
        embedding_mean: Iterable[float],
        samples: Dict[str, Any],
        threshold: float,
        *,
        source: str = "manual",
        append: bool = False,
        capture_id: str | None = None,
        session_id: str | None = None,
    ) -> Dict[str, Any]:
        return self.save(
            user_id,
            embedding_mean,
            samples,
            threshold,
            source=source,
            append=append,
            device_id=device_id,
            capture_id=capture_id,
            session_id=session_id,
        )

    def get_devices(self, user_id: str) -> Dict[str, Dict[str, Any]]:
        if self.graph:
            try:
                records = self.graph.get_active_records(user_id)
                if records:
                    devices: Dict[str, Dict[str, Any]] = {}
                    for record in records:
                        if record.get("scope") != "device":
                            continue
                        device_id = record.get("device_id") or ""
                        if device_id:
                            devices[device_id] = self._normalize_graph_record(record)
                    return devices
            except Exception:
                pass
        return self.db.fetch_device_voiceprints(user_id)

    def list_devices(self, user_id: str) -> List[str]:
        devices = self.get_devices(user_id)
        return list(devices.keys())

    def get_best(self, user_id: str) -> Dict[str, Any] | None:
        record = self.get(user_id)
        if record:
            record["device_id"] = "default"
            return record
        devices = self.get_devices(user_id)
        if not devices:
            return None
        first_device_id = next(iter(devices.keys()))
        first = devices[first_device_id]
        first["device_id"] = first_device_id
        return first

    def get_all_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        if self.graph:
            try:
                records = self.graph.get_active_records(user_id)
                if records:
                    result: List[Dict[str, Any]] = []
                    for record in records:
                        normalized = self._normalize_graph_record(record)
                        if normalized.get("scope") == "identity":
                            normalized["device_id"] = "default"
                        result.append(normalized)
                    return result
            except Exception:
                pass
        result = []
        base = self.db.fetch_voiceprint(user_id)
        if base:
            result.append({"device_id": "default", **base})
        devices = self.db.fetch_device_voiceprints(user_id)
        for device_id, record in devices.items():
            result.append({"device_id": device_id, **record})
        return result

    def get_historical_candidates(
        self,
        user_id: str,
        query_embedding: Iterable[float] | None = None,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        if self.graph:
            try:
                if query_embedding is not None:
                    records = self.graph.search_candidates(user_id, list(query_embedding), top_k=top_k)
                else:
                    records = self.graph.get_historical_candidates(user_id)
                if records:
                    return [self._normalize_candidate_record(record) for record in records]
            except Exception:
                pass
        return []

    def sample_count(self, user_id: str) -> int:
        record = self.get(user_id)
        if not record:
            return 0
        samples = record.get("samples") or {}
        return len(samples.get("samples") or [])

    def list_users(self) -> List[str]:
        users: set[str] = set()
        if self.graph:
            try:
                users.update(self.graph.list_user_ids())
            except Exception:
                pass
        cur = self.db.conn.cursor()
        cur.execute("SELECT user_id FROM voiceprints")
        users.update(row[0] for row in cur.fetchall())
        cur.execute("SELECT DISTINCT user_id FROM voiceprint_devices")
        users.update(row[0] for row in cur.fetchall())
        return sorted(users)

    def delete_device(self, user_id: str, device_id: str) -> bool:
        graph_deleted = False
        if self.graph:
            try:
                graph_deleted = self.graph.delete_device_voiceprint(user_id, device_id)
            except Exception:
                graph_deleted = False
        cur = self.db.conn.cursor()
        cur.execute("DELETE FROM voiceprint_devices WHERE user_id=? AND device_id=?", (user_id, device_id))
        self.db.conn.commit()
        return graph_deleted or cur.rowcount > 0

    def record_device_outcome(self, device_id: str, score: float, accepted: bool, alpha: float = 0.1) -> None:
        if not device_id:
            return
        self.db.record_device_outcome(device_id, float(score), bool(accepted), alpha=alpha)

    def fetch_device_calibration(self, device_id: str) -> Dict[str, Any] | None:
        if not device_id:
            return None
        return self.db.fetch_device_calibration(device_id)

    def backfill_global_speaker_embeddings(self, match_threshold: float | None = None) -> Dict[str, Any]:
        if not self.graph:
            return {"ok": False, "error": "Neo4j not configured"}
        return {"ok": True, **self.graph.backfill_global_speaker_embeddings(match_threshold=match_threshold)}
