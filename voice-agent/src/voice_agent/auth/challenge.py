from __future__ import annotations

import random
from pathlib import Path
from typing import List


DEFAULT_PHRASES = [
    "The quick brown fox jumps over the lazy dog",
    "I am speaking to verify my identity",
    "Voice authentication is active",
]


def load_phrases(path: str | None) -> List[str]:
    if not path:
        return DEFAULT_PHRASES
    try:
        text = Path(path).read_text(encoding="utf-8")
        phrases = [line.strip() for line in text.splitlines() if line.strip()]
        return phrases or DEFAULT_PHRASES
    except Exception:
        return DEFAULT_PHRASES


def random_phrase(path: str | None) -> str:
    phrases = load_phrases(path)
    return random.choice(phrases)
