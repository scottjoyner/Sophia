from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..util.db import Database
from ..util.time import now_ms
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
    def _sample_count(samples: dict[str, Any] | None) -> int:
        if not samples:
            return 0
        return len(samples.get("samples") or [])

    @staticmethod
    def _sqlite_record(
        user_id: str,
        embedding_mean: Iterable[float],
        samples: dict[str, Any],
        threshold: float,
        *,
        device_id: str | None = None,
    ) -> dict[str, Any]:
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
    def _normalize_graph_record(record: dict[str, Any]) -> dict[str, Any]:
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
    def _normalize_candidate_record(record: dict[str, Any]) -> dict[str, Any]:
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
        samples: dict[str, Any],
        threshold: float,
        *,
        source: str = "manual",
        append: bool = False,
        device_id: str | None = None,
        capture_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        graph_result: dict[str, Any] | None = None
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

    def get(self, user_id: str) -> dict[str, Any] | None:
        if self.graph:
            try:
                record = self.graph.get_active_record(user_id)
                if record:
                    return self._normalize_graph_record(record)
            except Exception:
                pass
        return self.db.fetch_voiceprint(user_id)

    def get_device(self, user_id: str, device_id: str) -> dict[str, Any] | None:
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
        samples: dict[str, Any],
        threshold: float,
        *,
        source: str = "manual",
        append: bool = False,
        capture_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
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

    def ensure_user(
        self,
        user_id: str,
        *,
        device_id: str | None = None,
        verified: bool = False,
        source: str = "unknown_speaker_registration",
    ) -> dict[str, Any]:
        """W-14: ensure a profile exists for ``user_id`` as a registered (but
        unverified) speaker. If a profile already exists it is returned unchanged;
        otherwise an empty placeholder is created so subsequent verification /
        enrollment can populate the voiceprint. ``verified`` is stored as a flag
        on the record (default False → ``registered_user_unverified``)."""
        existing = self.get_device(user_id, device_id) if device_id else self.get(user_id)
        if existing:
            return existing
        placeholder_samples: dict[str, Any] = {
            "samples": [],
            "verified": verified,
            "source": source,
            "registered_ts_ms": now_ms(),
        }
        # No embedding yet — empty mean; verification will replace it.
        return self.save(
            user_id,
            [],
            placeholder_samples,
            float(getattr(self, "_default_threshold", 0.75)),
            source=source,
            device_id=device_id,
        )

    def get_devices(self, user_id: str) -> dict[str, dict[str, Any]]:
        if self.graph:
            try:
                records = self.graph.get_active_records(user_id)
                if records:
                    devices: dict[str, dict[str, Any]] = {}
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

    def list_devices(self, user_id: str) -> list[str]:
        devices = self.get_devices(user_id)
        return list(devices.keys())

    def get_best(self, user_id: str) -> dict[str, Any] | None:
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

    def get_all_for_user(self, user_id: str) -> list[dict[str, Any]]:
        if self.graph:
            try:
                records = self.graph.get_active_records(user_id)
                if records:
                    result: list[dict[str, Any]] = []
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
    ) -> list[dict[str, Any]]:
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

    def list_users(self) -> list[str]:
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

    def fetch_device_calibration(self, device_id: str) -> dict[str, Any] | None:
        if not device_id:
            return None
        return self.db.fetch_device_calibration(device_id)

    def backfill_global_speaker_embeddings(self, match_threshold: float | None = None) -> dict[str, Any]:
        if not self.graph:
            return {"ok": False, "error": "Neo4j not configured"}
        return {"ok": True, **self.graph.backfill_global_speaker_embeddings(match_threshold=match_threshold)}

    def _push_to_graph(self, user_id: str, device_id: str | None, rec: dict[str, Any], source: str) -> None:
        self.graph.save_voiceprint(
            user_id=user_id,
            embedding_mean=list(rec.get("embedding") or []),
            samples=rec.get("samples") or {"samples": []},
            threshold=float(rec.get("threshold") or 0.6),
            source=source,
            append=False,
            device_id=None if device_id in (None, "default") else device_id,
        )

    def reconcile_to_neo4j(
        self, source: str = "reconcile", force: bool = False, check_only: bool = False
    ) -> dict[str, Any]:
        """Reconcile the local SQLite voiceprint store (the source of truth for
        enrollments performed on this node) against Neo4j.

        SQLite is treated as canonical: any ``(user, device)`` present locally
        but missing or differing in Neo4j is (re)pushed so the graph catches up
        after enrollments that happened while Neo4j was unavailable. Records that
        exist only in Neo4j (and therefore cannot be repaired from the local
        store) are reported under ``graph_only`` for manual review.

        Pass ``check_only=True`` to compute the drift report without mutating
        Neo4j.
        """
        if not self.graph:
            return {"ok": False, "error": "Neo4j not configured"}

        def _threshold(rec: dict[str, Any]) -> float:
            return float(rec.get("threshold") or 0.6)

        def _embedding(rec: dict[str, Any]) -> list[float]:
            return list(rec.get("embedding") or [])

        summary: dict[str, Any] = {
            "ok": True,
            "synced": 0,
            "skipped": 0,
            "drift": 0,
            "graph_only": 0,
            "errors": 0,
            "check_only": check_only,
            "users": [],
        }

        for uid in self.list_users():
            try:
                entry: dict[str, Any] = {"user_id": uid, "synced": 0, "skipped": 0, "drift": 0, "graph_only": 0}
                sqlite_recs: list[tuple[str | None, dict[str, Any]]] = []
                identity = self.db.fetch_voiceprint(uid)
                if identity:
                    sqlite_recs.append((None, identity))
                for device_id, rec in self.db.fetch_device_voiceprints(uid).items():
                    sqlite_recs.append((device_id, rec))

                graph_by_key: dict[tuple[str, str], dict[str, Any]] = {}
                for grec in self.graph.get_active_records(uid):
                    did = grec.get("device_id") or "default"
                    scope = "identity" if did == "default" else "device"
                    graph_by_key[(scope, did)] = grec

                synced_keys: set[tuple[str, str]] = set()
                for device_id, rec in sqlite_recs:
                    key = ("identity" if device_id in (None, "default") else "device", device_id or "default")
                    synced_keys.add(key)
                    grec = graph_by_key.get(key)
                    if grec is None:
                        if not check_only:
                            self._push_to_graph(uid, device_id, rec, source)
                        summary["synced"] += 1
                        entry["synced"] += 1
                        continue
                    same_emb = _embedding(grec) == _embedding(rec)
                    same_thr = _threshold(grec) == _threshold(rec)
                    if same_emb and same_thr and not force:
                        summary["skipped"] += 1
                        entry["skipped"] += 1
                        continue
                    if not (same_emb and same_thr):
                        summary["drift"] += 1
                        entry["drift"] += 1
                    if not check_only:
                        self._push_to_graph(uid, device_id, rec, source)
                    summary["synced"] += 1
                    entry["synced"] += 1

                for (scope, did) in graph_by_key:
                    if (scope, did) in synced_keys:
                        continue
                    summary["graph_only"] += 1
                    entry["graph_only"] += 1

                summary["users"].append(entry)
            except Exception as exc:
                summary["errors"] += 1
                summary["users"].append({"user_id": uid, "error": f"{type(exc).__name__}: {exc}"})
        return summary
