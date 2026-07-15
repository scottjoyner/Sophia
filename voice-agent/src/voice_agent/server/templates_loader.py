from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Dict

_TEMPLATES_DIR = Path(__file__).parent / "templates"

_MOBILE_RE = re.compile(
    r"(iphone|ipad|ipod|android|blackberry|bb10|mini|windows\s+phone|opera\s+mini|mobile|tablet|kindle|playbook|silk)",
    re.I,
)


def is_mobile_user_agent(ua: str | None) -> bool:
    if not ua:
        return False
    return bool(_MOBILE_RE.search(ua))


@lru_cache(maxsize=8)
def load_template(name: str) -> str:
    path = _TEMPLATES_DIR / name
    return path.read_text(encoding="utf-8")
