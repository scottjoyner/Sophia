from __future__ import annotations

from pathlib import Path
from typing import List

import numpy as np

from ..config import AppConfig
from ..util.audio import read_wav
from .registry import VoiceprintRegistry
from .speaker_embedder import SpeakerEmbedder


def enroll_from_files(config: AppConfig, user_id: str, files: List[str]) -> None:
    registry = VoiceprintRegistry(Path(config.paths.artifacts_dir) / "results.sqlite")
    embedder = SpeakerEmbedder()
    embeddings = []
    samples_meta = []
    for path in files:
        audio, sr = read_wav(path)
        embedding = embedder.embed(audio, sr)
        embeddings.append(embedding)
        samples_meta.append({"path": path, "sample_rate": sr})
    embedding_mean = np.mean(np.array(embeddings, dtype=float), axis=0).tolist()
    registry.save(user_id, embedding_mean, {"samples": samples_meta}, config.auth.threshold)
