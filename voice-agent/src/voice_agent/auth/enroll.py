from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from ..config import AppConfig
from ..util.audio import read_wav
from ..util.time import now_ms
from .registry import VoiceprintRegistry
from .speaker_embedder import SpeakerEmbedder


class EnrollmentError(RuntimeError):
    pass


def _fingerprint_file(path: str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sample_seconds(audio: np.ndarray, sample_rate: int) -> float:
    if sample_rate <= 0:
        return 0.0
    return float(len(audio) / sample_rate)


def _validate_clip(path: str, audio: np.ndarray, sample_rate: int, min_seconds: float, max_seconds: float) -> Dict[str, Any]:
    duration = _sample_seconds(audio, sample_rate)
    if duration < min_seconds:
        raise EnrollmentError(f"{path} is too short for enrollment: {duration:.2f}s < {min_seconds:.2f}s")
    if duration > max_seconds:
        raise EnrollmentError(f"{path} is too long for direct enrollment: {duration:.2f}s > {max_seconds:.2f}s")
    energy = float(np.mean(np.abs(audio))) if audio.size else 0.0
    if energy < 0.001:
        raise EnrollmentError(f"{path} appears silent or near-silent")
    return {"duration_seconds": duration, "energy": energy}


def enroll_from_files(
    config: AppConfig,
    user_id: str,
    files: List[str],
    *,
    append: bool = False,
    source: str = "manual",
    min_seconds: float = 2.0,
    max_seconds: float = 30.0,
) -> Dict[str, Any]:
    registry = VoiceprintRegistry(Path(config.paths.artifacts_dir) / "results.sqlite")
    embedder = SpeakerEmbedder()
    existing = registry.get(user_id) if append else None
    existing_samples = ((existing or {}).get("samples") or {}).get("samples") or []
    existing_by_sha = {sample.get("sha256") for sample in existing_samples if sample.get("sha256")}

    embeddings: List[List[float]] = []
    samples_meta: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []

    # Preserve existing sample weights by replaying stored per-sample embeddings when available.
    for sample in existing_samples:
        embedding = sample.get("embedding")
        if isinstance(embedding, list) and embedding:
            embeddings.append(embedding)
            samples_meta.append(sample)

    for path in files:
        try:
            sha256 = _fingerprint_file(path)
            if sha256 in existing_by_sha:
                continue
            audio, sr = read_wav(path)
            quality = _validate_clip(path, audio, sr, min_seconds=min_seconds, max_seconds=max_seconds)
            embedding = embedder.embed(audio, sr)
            embeddings.append(embedding)
            samples_meta.append(
                {
                    "path": path,
                    "sample_rate": sr,
                    "sha256": sha256,
                    "source": source,
                    "added_ts_ms": now_ms(),
                    "duration_seconds": quality["duration_seconds"],
                    "energy": quality["energy"],
                    "embedding": embedding,
                }
            )
        except Exception as exc:
            errors.append({"path": path, "error": str(exc)})

    if not embeddings:
        raise EnrollmentError("No usable voice clips were available for enrollment")

    embedding_mean = np.mean(np.array(embeddings, dtype=float), axis=0).tolist()
    metadata = {
        "samples": samples_meta,
        "sample_count": len(samples_meta),
        "append": append,
        "source": source,
        "updated_ts_ms": now_ms(),
        "errors": errors,
    }
    registry.save(user_id, embedding_mean, metadata, config.auth.threshold)
    return {
        "user_id": user_id,
        "sample_count": len(samples_meta),
        "new_files_requested": len(files),
        "new_files_failed": len(errors),
        "appended": append,
        "threshold": config.auth.threshold,
        "errors": errors,
    }
