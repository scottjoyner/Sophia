from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List

from ..util.db import Database


class VoiceprintRegistry:
    def __init__(self, db_path: Path):
        self.db = Database(db_path)

    def save(self, user_id: str, embedding_mean: Iterable[float], samples: Dict[str, Any], threshold: float) -> None:
        self.db.save_voiceprint(user_id, embedding_mean, samples, threshold)

    def get(self, user_id: str) -> Dict[str, Any] | None:
        return self.db.fetch_voiceprint(user_id)

<<<<<<< HEAD
    def save_device(self, user_id: str, device_id: str, embedding_mean: Iterable[float], samples: Dict[str, Any], threshold: float) -> None:
        self.db.save_device_voiceprint(user_id, device_id, embedding_mean, samples, threshold)

    def get_devices(self, user_id: str) -> Dict[str, Dict[str, Any]]:
        return self.db.fetch_device_voiceprints(user_id)

    def list_devices(self, user_id: str) -> List[str]:
        return self.db.list_device_ids(user_id)

    def get_best(self, user_id: str) -> Dict[str, Any] | None:
        base = self.get(user_id)
        if base:
            base["device_id"] = "default"
            return base
        devices = self.get_devices(user_id)
        if not devices:
            return None
        first = next(iter(devices.values()))
        first["device_id"] = next(iter(devices.keys()))
        return first

    def get_all_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        result = []
        base = self.get(user_id)
        if base:
            result.append({"device_id": "default", **base})
        devices = self.get_devices(user_id)
        for device_id, record in devices.items():
            result.append({"device_id": device_id, **record})
        return result
=======
    def sample_count(self, user_id: str) -> int:
        record = self.get(user_id)
        if not record:
            return 0
        samples = record.get("samples") or {}
        return len(samples.get("samples") or [])
>>>>>>> 4caa783d8510f01247862aecb521c50c82cd9f9c
