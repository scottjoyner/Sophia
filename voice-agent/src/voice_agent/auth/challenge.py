from __future__ import annotations

import random
from pathlib import Path

DEFAULT_PHRASES = [
    "The quick brown fox jumps over the lazy dog",
    "I am speaking to verify my identity",
    "Voice authentication is active",
]


class ChallengePhraseUnavailableError(RuntimeError):
    """A configured challenge-phrase file could not be used."""


def load_phrases(path: str | None) -> list[str]:
    # No path is an explicit opt-out and keeps the built-in defaults.
    if not path:
        return DEFAULT_PHRASES
    # Once a path IS configured, falling back to the public DEFAULT_PHRASES on
    # a read failure or empty file would silently make the voice challenge
    # predictable. Fail closed instead (R43).
    try:
        text = Path(path).read_text(encoding="utf-8")
    except Exception as exc:
        raise ChallengePhraseUnavailableError(
            f"configured challenge phrase file could not be read: {path}"
        ) from exc
    phrases = [line.strip() for line in text.splitlines() if line.strip()]
    if not phrases:
        raise ChallengePhraseUnavailableError(
            f"configured challenge phrase file is empty: {path}"
        )
    return phrases


def random_phrase(path: str | None) -> str:
    phrases = load_phrases(path)
    return random.choice(phrases)
