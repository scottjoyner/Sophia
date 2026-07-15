from __future__ import annotations

from pathlib import Path

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


def _score_record(embedding: np.ndarray, record: dict[str, object]) -> float | None:
    stored = np.array(record.get("embedding") or [], dtype=float).ravel()
    if stored.size == 0 or stored.shape != embedding.shape:
        return None
    return cosine_similarity(embedding, stored)


def _candidate_threshold(record: dict[str, object], default_threshold: float) -> float:
    threshold = record.get("threshold")
    if isinstance(threshold, (int, float)):
        return float(threshold)
    return float(default_threshold)


def _compute_adaptive_threshold(base: float, calibration: dict[str, object] | None, config: AppConfig) -> float:
    if not config.auth.adaptive_threshold_enabled:
        return float(base)
    if not calibration:
        return float(base)
    accepted_mean = calibration.get("accepted_mean")
    rejected_mean = calibration.get("rejected_mean")
    if accepted_mean is None:
        return float(base)
    candidate = float(accepted_mean) - config.auth.adaptive_threshold_margin
    if rejected_mean is not None:
        candidate = max(candidate, float(rejected_mean) + config.auth.adaptive_threshold_margin)
    return min(max(candidate, config.auth.adaptive_threshold_min), config.auth.adaptive_threshold_max)


def _effective_threshold(
    record: dict[str, object],
    default_threshold: float,
    config: AppConfig,
    registry: VoiceprintRegistry | None,
) -> float:
    base = _candidate_threshold(record, default_threshold)
    device_id = record.get("device_id")
    if not device_id or device_id in {"default"}:
        return base
    if not config.auth.adaptive_threshold_enabled or registry is None:
        return base
    calibration = registry.fetch_device_calibration(str(device_id))
    return _compute_adaptive_threshold(base, calibration, config)


def _build_candidate(record: dict[str, object], score: float, threshold: float) -> dict[str, object]:
    return {
        "candidate_id": record.get("candidate_id") or record.get("version_id"),
        "candidate_type": record.get("candidate_type") or "version",
        "version_id": record.get("version_id"),
        "group_key": record.get("group_key"),
        "scope": record.get("scope"),
        "device_id": record.get("device_id"),
        "sample_id": record.get("sample_id"),
        "sample_sha256": record.get("sample_sha256"),
        "sample_path": record.get("sample_path"),
        "sample_source": record.get("sample_source"),
        "score": score,
        "threshold": threshold,
        "accepted": score >= threshold,
        "active": bool(record.get("active", True)),
        "lineage_mode": record.get("lineage_mode"),
        "source": record.get("source"),
    }


def verify_audio_segment(
    config: AppConfig, session_id: str, user_id: str, samples: np.ndarray, sample_rate: int
) -> dict[str, object]:
    registry = VoiceprintRegistry(Path(config.paths.artifacts_dir) / "results.sqlite", config)
    embedder = SpeakerEmbedder()
    challenge_phrase = None
    if config.auth.require_challenge:
        challenge_phrase = random_phrase(config.auth.challenge_phrases_file)

    embedding = np.array(embedder.embed(samples, sample_rate), dtype=float)
    active_records = registry.get_all_for_user(user_id)
    active_candidates: list[tuple[float, dict[str, object]]] = []
    for record in active_records:
        score = _score_record(embedding, record)
        if score is None:
            continue
        active_candidates.append((score, record))

    active_candidates.sort(key=lambda item: item[0], reverse=True)
    best_active_score = active_candidates[0][0] if active_candidates else 0.0
    best_active_record = active_candidates[0][1] if active_candidates else None
    active_threshold = _effective_threshold(best_active_record or {}, config.auth.threshold, config, registry)
    active_accepted = best_active_record is not None and best_active_score >= active_threshold

    historical_candidates: list[dict[str, object]] = []
    best_fallback_candidate: dict[str, object] | None = None
    fallback_score = 0.0
    fallback_used = False
    fallback_reason = None

    if not active_accepted:
        fallback_reason = "active_head_below_threshold" if best_active_record else "no_active_match"
        candidate_records = registry.get_historical_candidates(user_id, embedding, top_k=5)
        seen: set[str] = set()
        scored_candidates: list[dict[str, object]] = []
        for record in candidate_records:
            candidate_id = str(record.get("candidate_id") or "")
            version_id = str(record.get("version_id") or "")
            if not candidate_id:
                continue
            if best_active_record and record.get("candidate_type") == "version" and version_id == best_active_record.get("version_id"):
                continue
            if candidate_id in seen:
                continue
            score = _score_record(embedding, record)
            if score is None:
                continue
            seen.add(candidate_id)
            threshold = _effective_threshold(record, config.auth.threshold, config, registry)
            scored = _build_candidate(record, score, threshold)
            scored_candidates.append(scored)
        scored_candidates.sort(key=lambda item: item["score"], reverse=True)
        historical_candidates = scored_candidates[:5]
        if historical_candidates:
            best_fallback_candidate = historical_candidates[0]
            fallback_score = float(best_fallback_candidate["score"])
            fallback_used = True
            if fallback_score >= float(best_fallback_candidate["threshold"]):
                active_accepted = True
        else:
            fallback_reason = fallback_reason or "no_historical_candidates"

    selected_record = best_active_record or {}
    selected_score = best_active_score
    match_source = "active_head"
    if fallback_used and best_fallback_candidate:
        selected_record = {
            "version_id": best_fallback_candidate.get("version_id"),
            "group_key": best_fallback_candidate.get("group_key"),
            "scope": best_fallback_candidate.get("scope"),
            "device_id": best_fallback_candidate.get("device_id"),
        }
        selected_score = fallback_score
        match_source = "historical_fallback"

    effective_threshold = active_threshold
    adaptive_calibration = None
    calibrated_device_id = selected_record.get("device_id")
    if calibrated_device_id and calibrated_device_id not in {"default"}:
        adaptive_calibration = registry.fetch_device_calibration(str(calibrated_device_id))
        effective_threshold = _compute_adaptive_threshold(
            _candidate_threshold(selected_record or {}, config.auth.threshold),
            adaptive_calibration,
            config,
        )
        try:
            registry.record_device_outcome(
                str(calibrated_device_id),
                float(selected_score),
                bool(active_accepted),
                alpha=config.auth.adaptive_threshold_alpha,
            )
        except Exception:
            pass

    return {
        "session_id": session_id,
        "user_id": user_id,
        "score": selected_score,
        "primary_score": best_active_score,
        "fallback_score": fallback_score if fallback_used else None,
        "accepted": active_accepted,
        "match_source": match_source,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "challenge": challenge_phrase,
        "device_id": calibrated_device_id,
        "threshold_used": effective_threshold,
        "adaptive_threshold": effective_threshold if adaptive_calibration else None,
        "device_calibration": adaptive_calibration,
        "voiceprint_version_id": selected_record.get("version_id") if selected_record else None,
        "voiceprint_group_key": selected_record.get("group_key") if selected_record else None,
        "voiceprint_scope": selected_record.get("scope") if selected_record else None,
        "voiceprint_match_type": best_fallback_candidate.get("candidate_type") if best_fallback_candidate else ("version" if best_active_record else None),
        "voiceprint_candidate_ids": [candidate["candidate_id"] for candidate in historical_candidates],
        "voiceprint_candidate_scores": [candidate["score"] for candidate in historical_candidates],
        "voiceprint_candidates": historical_candidates,
        "ts_ms": now_ms(),
    }
