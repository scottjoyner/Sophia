from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable

from ..util.db import Database


class VoiceprintRegistry:
    def __init__(self, db_path: Path):
        self.db = Database(db_path)

    def save(self, user_id: str, embedding_mean: Iterable[float], samples: Dict[str, Any], threshold: float) -> None:
        self.db.save_voiceprint(user_id, embedding_mean, samples, threshold)

    def get(self, user_id: str) -> Dict[str, Any] | None:
        return self.db.fetch_voiceprint(user_id)
