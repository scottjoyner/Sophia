from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np

from ..config import AppConfig
from ..util.audio import read_wav
from ..util.time import now_ms
from .registry import VoiceprintRegistry
from .speaker_embedder import SpeakerEmbedder


class EnrollmentError(RuntimeError):
    pass


def register_unknown_speaker(
    config: AppConfig,
    *,
    user_id: str,
    enrollment_token: str | None = None,
    device_id: str | None = None,
    source: str = "unknown_speaker_registration",
) -> dict[str, Any]:
    """W-14: register a previously-unknown speaker as a pending, unverified profile.

    The profile is created with ``auth_state`` semantics of
    ``registered_user_unverified``: it exists in the registry but has no
    confident voiceprint yet, so it cannot authenticate until a verification
    sample is supplied (and optionally an enrollment token is validated). This
    is the entry point for the unknown-speaker registration flow.

    Returns a dict carrying ``auth_state = "registered_user_unverified"`` so the
    caller can emit the correct contract envelope.
    """
    if not user_id or user_id == config.auth.owner_user_id:
        raise EnrollmentError("cannot register the owner as an unknown speaker")
    configured_token = config.auth.enrollment_token
    if configured_token and enrollment_token != configured_token:
        raise EnrollmentError("invalid enrollment token")

    registry = VoiceprintRegistry(Path(config.paths.artifacts_dir) / "results.sqlite", config)
    # Create an empty, unverified profile placeholder. No voiceprint embedding
    # yet; verification/collection will populate it later.
    record = registry.ensure_user(
        user_id,
        device_id=device_id,
        verified=False,
        source=source,
    )
    return {
        "ok": True,
        "user_id": user_id,
        "device_id": device_id,
        "auth_state": "registered_user_unverified",
        "verified": bool(record.get("verified")) if isinstance(record, dict) else False,
        "source": source,
    }


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


def _validate_clip(path: str, audio: np.ndarray, sample_rate: int, min_seconds: float, max_seconds: float) -> dict[str, Any]:
    duration = _sample_seconds(audio, sample_rate)
    if duration < min_seconds:
        raise EnrollmentError(f"{path} is too short for enrollment: {duration:.2f}s < {min_seconds:.2f}s")
    if duration > max_seconds:
        raise EnrollmentError(f"{path} is too long for direct enrollment: {duration:.2f}s > {max_seconds:.2f}s")
    energy = float(np.mean(np.abs(audio))) if audio.size else 0.0
    if energy < 0.001:
        raise EnrollmentError(f"{path} appears silent or near-silent")
    return {"duration_seconds": duration, "energy": energy}


def _prune_outlier_embeddings(
    embeddings: list[list[float]],
    samples_meta: list[dict[str, Any]],
    reference: np.ndarray | None = None,
    threshold: float = 0.5,
) -> tuple[list[list[float]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Drop enrollment clips whose embedding is far from the consensus.

    Used before averaging so a single bad clip (noise, cross-talk, wrong
    speaker) cannot poison the stored voiceprint mean. Returns the kept
    embeddings, kept metadata, and the rejected metadata.
    """
    if len(embeddings) < 3:
        return embeddings, samples_meta, []
    arr = np.asarray(embeddings, dtype=float)
    if arr.ndim != 2 or arr.shape[1] == 0:
        return embeddings, samples_meta, []
    norms = np.linalg.norm(arr, axis=1)
    safe = np.where(norms == 0, 1.0, norms)
    unit = arr / safe[:, None]
    if reference is None:
        ref = np.median(unit, axis=0)
    else:
        ref = np.asarray(reference, dtype=float)
    ref_norm = np.linalg.norm(ref)
    if ref_norm == 0:
        return embeddings, samples_meta, []
    ref = ref / ref_norm
    sims = unit @ ref
    keep = sims >= threshold
    if not keep.any():
        return embeddings, samples_meta, []
    kept_emb = [e for e, k in zip(embeddings, keep, strict=True) if k]
    kept_meta = [m for m, k in zip(samples_meta, keep, strict=True) if k]
    rejected = [m for m, k in zip(samples_meta, keep, strict=True) if not k]
    return kept_emb, kept_meta, rejected


def enroll_from_files(
    config: AppConfig,
    user_id: str,
    files: list[str],
    *,
    append: bool = False,
    source: str = "manual",
    min_seconds: float = 2.0,
    max_seconds: float = 30.0,
    device_id: str | None = None,
) -> dict[str, Any]:
    registry = VoiceprintRegistry(Path(config.paths.artifacts_dir) / "results.sqlite", config)
    embedder = SpeakerEmbedder()

    # In append mode, we load existing samples to recalculate the mean and avoid duplicates.
    existing = None
    if append:
        if device_id:
            existing = registry.get_device(user_id, device_id)
        else:
            existing = registry.get(user_id)

    existing_samples = ((existing or {}).get("samples") or {}).get("samples") or []
    existing_by_sha = {sample.get("sha256") for sample in existing_samples if sample.get("sha256")}

    embeddings: list[list[float]] = []
    samples_meta: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
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

    baseline_embeddings = list(embeddings)
    baseline_meta = list(samples_meta)

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

    rejected_outliers: list[dict[str, Any]] = []
    new_embeddings = embeddings[len(baseline_embeddings):]
    if len(new_embeddings) >= 3:
        reference = (
            np.mean(np.asarray(baseline_embeddings, dtype=float), axis=0)
            if baseline_embeddings
            else None
        )
        kept_new, kept_new_meta, rejected = _prune_outlier_embeddings(
            new_embeddings, samples_meta[len(baseline_meta):], reference=reference
        )
        rejected_outliers = rejected
        embeddings = baseline_embeddings + kept_new
        samples_meta = baseline_meta + kept_new_meta

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
        "legacy_mean_preserved": legacy_mean_preserved,
        "rejected_outliers": [r.get("path", "<stored>") for r in rejected_outliers],
    }

    saved_record = (
        registry.save_device(
            user_id,
            device_id,
            embedding_mean,
            metadata,
            config.auth.threshold,
            source=source,
            append=append,
        )
        if device_id
        else registry.save(
            user_id,
            embedding_mean,
            metadata,
            config.auth.threshold,
            source=source,
            append=append,
        )
    )

    return {
        "user_id": user_id,
        "device_id": device_id,
        "version_id": saved_record.get("version_id"),
        "group_key": saved_record.get("group_key"),
        "voiceprint_scope": saved_record.get("scope"),
        "lineage_mode": saved_record.get("lineage_mode"),
        "sample_count": len(samples_meta),
        "new_files_requested": len(files),
        "new_files_failed": len(errors),
        "appended": append,
        "threshold": config.auth.threshold,
        "legacy_mean_preserved": legacy_mean_preserved,
        "rejected_outliers": [r.get("path", "<stored>") for r in rejected_outliers],
        "errors": errors,
        "speaker_linkage": saved_record.get("speaker_linkage"),
        "graph_saved": bool(saved_record.get("graph_saved") or saved_record.get("captured_in_graph")),
        "graph_enabled": bool(saved_record.get("graph_enabled")),
        "graph_error": saved_record.get("graph_error"),
    }
