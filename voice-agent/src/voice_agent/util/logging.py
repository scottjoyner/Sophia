from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from .time import now_ms


class JsonlLogger:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event_type: str, payload: Dict[str, Any]) -> None:
        record = {
            "ts_ms": now_ms(),
            "type": event_type,
            "payload": payload,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
