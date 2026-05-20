from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List


def load_manifest(path: str) -> List[Dict[str, str]]:
    items = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        items.append(json.loads(line))
    return items
