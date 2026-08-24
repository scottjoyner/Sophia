#!/usr/bin/env python3
"""Audit enrolled voiceprint data — identify problematic training clips.

Reports:
- Per-device sample counts, sources, and date ranges
- Clips from known-wrong sources (e.g. 2025 dashcam marked as different speaker)
- Embedding outlier scores for quality assessment
- Recommended purge list

Usage:
    python scripts/audit_voiceprint_data.py --config configs/container.yaml
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from voice_agent.auth.registry import VoiceprintRegistry
from voice_agent.auth.speaker_embedder import SpeakerEmbedder
from voice_agent.config import load_config


def _load_registry(config_path: str):
    config = load_config(config_path)
    registry = VoiceprintRegistry(Path(config.paths.artifacts_dir) / "results.sqlite", config)
    return config, registry


def _describe_source(meta: dict) -> str:
    source = (meta.get("source") or meta.get("samples", {}).get("source") or "unknown")
    added_ts = meta.get("updated_ts_ms") or meta.get("added_ts_ms")
    return f"source={source}, ts_ms={added_ts}"


def _duration_range(samples: list[dict]) -> tuple[float, float, float]:
    durations = [s.get("duration_seconds", 0) or 0 for s in samples]
    if not durations:
        return (0, 0, 0)
    return (min(durations), max(durations), sum(durations) / len(durations))


def report(config_path: str):
    config, registry = _load_registry(config_path)
    embedder = SpeakerEmbedder(config)
    known_bad_sources = {"dashcam_2025", "2025_dashcam", "batch_2025"}
    known_bad_keys = {"2025"}

    print("=" * 72)
    print("Voiceprint Data Audit Report")
    print("=" * 72)

    for uid in registry.list_users():
        print(f"\n--- User: {uid} ---")
        identity = registry.get(uid)
        devices = registry.get_devices(uid)

        identity_samples = []
        if identity:
            identity_samples = (identity.get("samples") or {}).get("samples") or []
            dmin, dmax, dmean = _duration_range(identity_samples)
            print(f"  Identity scope: {len(identity_samples)} samples")
            print(f"    Duration range: {dmin:.1f}s - {dmax:.1f}s (mean {dmean:.1f}s)")
            src = _describe_source(identity)
            print(f"    Source: {src}")
            threshold = identity.get("threshold", "?")
            print(f"    Threshold: {threshold}")

        if devices:
            for dev_id, dev_rec in devices.items():
                samples = (dev_rec.get("samples") or {}).get("samples") or []
                dmin, dmax, dmean = _duration_range(samples)
                print(f"\n  Device: {dev_id} — {len(samples)} samples")
                print(f"    Duration range: {dmin:.1f}s - {dmax:.1f}s (mean {dmean:.1f}s)")
                src = _describe_source(dev_rec)
                print(f"    Source: {src}")
                threshold = dev_rec.get("threshold", "?")
                print(f"    Threshold: {threshold}")

        # Check for problematic clips
        all_samples = list(identity_samples)
        purge_candidates = []
        for s in all_samples:
            path = s.get("path", "")
            sha = s.get("sha256", "")[:16]
            source = s.get("source", "")
            duration = s.get("duration_seconds", 0)
            if any(bad in source for bad in known_bad_sources):
                purge_candidates.append({"path": path, "sha256": sha, "source": source, "reason": "known_bad_source", "duration": duration})
            elif any(bad in path for bad in known_bad_keys):
                purge_candidates.append({"path": path, "sha256": sha, "source": source, "reason": "year_match_2025", "duration": duration})
            elif duration is not None and duration < 2.0:
                purge_candidates.append({"path": path, "sha256": sha, "source": source, "reason": "too_short", "duration": duration})

        if purge_candidates:
            print(f"\n  [!] Potential purge candidates ({len(purge_candidates)}):")
            for pc in purge_candidates:
                print(f"      {pc['reason']:20s} {pc['duration']:6.1f}s  {pc['source'][:30]:30s}  {pc['sha256']}")
        else:
            print(f"\n  [✓] No purge candidates found")

        # Cross-check: score each sample embedding against the mean
        all_embeddings = []
        for s in all_samples:
            emb = s.get("embedding")
            if isinstance(emb, list) and len(emb) == 192:
                all_embeddings.append(np.array(emb, dtype=float))
        if len(all_embeddings) >= 3:
            mean_emb = np.mean(all_embeddings, axis=0)
            mean_norm = np.linalg.norm(mean_emb)
            if mean_norm > 0:
                mean_emb = mean_emb / mean_norm
            outliers = []
            for idx, emb in enumerate(all_embeddings):
                emb_norm = np.linalg.norm(emb)
                if emb_norm > 0:
                    sim = float(np.dot(emb / emb_norm, mean_emb))
                else:
                    sim = 0.0
                if sim < 0.5:
                    s_meta = all_samples[idx]
                    outliers.append({"idx": idx, "score": sim, "sha256": str(s_meta.get("sha256", ""))[:16], "source": s_meta.get("source", "")})
            if outliers:
                print(f"\n  [!] Low-similarity samples (<0.5 vs mean): {len(outliers)}")
                for o in outliers:
                    print(f"      score={o['score']:.3f}  {o['source'][:30]:30s}  {o['sha256']}")
            else:
                print(f"  [✓] All {len(all_embeddings)} samples consistent with mean (>=0.5)")

    print("\n" + "=" * 72)
    print("Audit complete.")
    print("=" * 72)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit voiceprint training data quality")
    parser.add_argument("--config", default="configs/container.yaml", help="Config file path")
    args = parser.parse_args()
    report(args.config)
