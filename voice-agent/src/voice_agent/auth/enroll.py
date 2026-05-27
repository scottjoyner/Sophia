from __future__ import annotations

import hashlib
from pathlib import Path
<<<<<<< HEAD
from typing import List, Optional
=======
from typing import Any, Dict, List
>>>>>>> 4caa783d8510f01247862aecb521c50c82cd9f9c

import numpy as np

from ..config import AppConfig
from ..util.audio import read_wav
from ..util.time import now_ms
from .registry import VoiceprintRegistry
from .speaker_embedder import SpeakerEmbedder


<<<<<<< HEAD
def enroll_from_files(
    config: AppConfig, user_id: str, files: List[str], device_id: Optional[str] = None
) -> None:
=======
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
>>>>>>> 4caa783d8510f01247862aecb521c50c82cd9f9c
    registry = VoiceprintRegistry(Path(config.paths.artifacts_dir) / "results.sqlite")
    embedder = SpeakerEmbedder()
    existing = registry.get(user_id) if append else None
    existing_samples = ((existing or {}).get("samples") or {}).get("samples") or []
    existing_by_sha = {sample.get("sha256") for sample in existing_samples if sample.get("sha256")}

    embeddings: List[List[float]] = []
    samples_meta: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    legacy_mean_preserved = False

    # Preserve existing sample weights by replaying stored per-sample embeddings when available.
    for sample in existing_samples:
        embedding = sample.get("embedding")
        if isinstance(embedding, list) and embedding:
            embeddings.append(embedding)
            samples_meta.append(sample)

    # Older voiceprints did not store per-sample embeddings. Keep the old mean as a
    # single legacy prior so the first append does not accidentally discard the
    # current known voiceprint while adding recovery clips.
    if existing and not embeddings and isinstance(existing.get("embedding"), list) and existing.get("embedding"):
        embeddings.append(existing["embedding"])
        samples_meta.extend(existing_samples)
        legacy_mean_preserved = True

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
<<<<<<< HEAD
    if device_id:
        registry.save_device(user_id, device_id, embedding_mean, {"samples": samples_meta}, config.auth.threshold)
    else:
        registry.save(user_id, embedding_mean, {"samples": samples_meta}, config.auth.threshold)
=======
    metadata = {
        "samples": samples_meta,
        "sample_count": len(samples_meta),
        "append": append,
        "source": source,
        "updated_ts_ms": now_ms(),
        "errors": errors,
        "legacy_mean_preserved": legacy_mean_preserved,
    }
    registry.save(user_id, embedding_mean, metadata, config.auth.threshold)
    return {
        "user_id": user_id,
        "sample_count": len(samples_meta),
        "new_files_requested": len(files),
        "new_files_failed": len(errors),
        "appended": append,
        "threshold": config.auth.threshold,
        "legacy_mean_preserved": legacy_mean_preserved,
        "errors": errors,
    }
>>>>>>> 4caa783d8510f01247862aecb521c50c82cd9f9c
