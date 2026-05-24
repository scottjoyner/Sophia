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

    all_records = registry.get_all_for_user(user_id)
    if not all_records:
        return {
            "session_id": session_id,
            "user_id": user_id,
            "score": 0.0,
            "accepted": False,
            "challenge": challenge_phrase,
            "device_id": None,
            "ts_ms": now_ms(),
        }

    embedding = np.array(embedder.embed(samples, sample_rate), dtype=float)
    best_score = 0.0
    best_record = all_records[0]
    for record in all_records:
        stored = np.array(record["embedding"], dtype=float).ravel()
        if stored.shape != embedding.shape:
            continue
        score = cosine_similarity(embedding, stored)
        if score > best_score:
            best_score = score
            best_record = record

    accepted = best_score >= best_record["threshold"]
    return {
        "session_id": session_id,
        "user_id": user_id,
        "score": best_score,
        "accepted": accepted,
        "challenge": challenge_phrase,
        "device_id": best_record["device_id"],
        "ts_ms": now_ms(),
    }
