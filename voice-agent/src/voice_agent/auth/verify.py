from __future__ import annotations

from pathlib import Path
from typing import Dict

import numpy as np

from ..config import AppConfig
from ..util.time import now_ms
from .challenge import random_phrase
from .registry import VoiceprintRegistry
from .speaker_embedder import SpeakerEmbedder


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    if np.linalg.norm(a) == 0 or np.linalg.norm(b) == 0:
        return 0.0
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def verify_audio_segment(
    config: AppConfig, session_id: str, user_id: str, samples: np.ndarray, sample_rate: int
) -> Dict[str, object]:
    registry = VoiceprintRegistry(Path(config.paths.artifacts_dir) / "results.sqlite")
    embedder = SpeakerEmbedder()
    challenge_phrase = None
    if config.auth.require_challenge:
        challenge_phrase = random_phrase(config.auth.challenge_phrases_file)
    record = registry.get(user_id)
    if not record:
        return {
            "session_id": session_id,
            "user_id": user_id,
            "score": 0.0,
            "accepted": False,
            "challenge": challenge_phrase,
            "ts_ms": now_ms(),
        }
    embedding = embedder.embed(samples, sample_rate)
    score = cosine_similarity(np.array(embedding, dtype=float), np.array(record["embedding"], dtype=float))
    accepted = score >= record["threshold"]
    return {
        "session_id": session_id,
        "user_id": user_id,
        "score": score,
        "accepted": accepted,
        "challenge": challenge_phrase,
        "ts_ms": now_ms(),
    }
