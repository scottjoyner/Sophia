from __future__ import annotations

import json
from pathlib import Path


def load_manifest(path: str) -> list[dict[str, str]]:
    items = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        items.append(json.loads(line))
    return items
