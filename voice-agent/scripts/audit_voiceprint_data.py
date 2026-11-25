#!/usr/bin/env python3
"""Audit enrolled voiceprint data for poisoning / quality problems (read-only).

Reports, per user and per device scope:
  - sample counts, duration ranges, and the recorded source of every clip
  - clips flagged by caller-supplied "known bad" markers (source or path substrings)
  - per-sample embedding outliers (cosine similarity against the mean embedding)

It never writes. Everything is read through VoiceprintRegistry, which prefers the
Neo4j voiceprint graph and falls back to SQLite, so this audits whichever store
the deployment actually verifies against.

Why this exists: an unauthenticated /voiceprints/enroll can append clips to a
voiceprint. When you are auditing a suspected poisoning, you need to see which
clips are in there, where they came from, and which ones do not look like the
rest of the profile.

Examples:

    python scripts/audit_voiceprint_data.py --config configs/container.yaml
    python scripts/audit_voiceprint_data.py --bad-source dashcam --bad-source batch_2025
    python scripts/audit_voiceprint_data.py --user scott --outlier-threshold 0.5 --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from voice_agent.auth.registry import VoiceprintRegistry  # noqa: E402
from voice_agent.config import load_config  # noqa: E402

# Fallback embedder dimension (see auth/speaker_embedder.py); used only to skip
# vectors from a different embedder rather than to interpret them.
EMBEDDING_DIMENSION = 192


def _samples(record: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not record:
        return []
    samples = record.get("samples")
    if not isinstance(samples, dict):
        return []
    return list(samples.get("samples") or [])


def _duration_range(samples: list[dict[str, Any]]) -> tuple[float | None, float | None, float | None]:
    durations = [s.get("duration_seconds") for s in samples if isinstance(s.get("duration_seconds"), (int, float))]
    if not durations:
        return (None, None, None)
    return (min(durations), max(durations), sum(durations) / len(durations))


def _scope_summary(
    record: dict[str, Any],
    *,
    bad_sources: tuple[str, ...],
    bad_path_tokens: tuple[str, ...],
    outlier_threshold: float,
    min_seconds: float,
) -> dict[str, Any]:
    samples = _samples(record)
    dmin, dmax, dmean = _duration_range(samples)

    flagged: list[dict[str, Any]] = []
    for sample in samples:
        source = str(sample.get("source") or "")
        path = str(sample.get("path") or "")
        duration = sample.get("duration_seconds")
        reason = None
        if any(marker in source for marker in bad_sources):
            reason = "known_bad_source"
        elif any(marker in path for marker in bad_path_tokens):
            reason = "known_bad_path"
        elif isinstance(duration, (int, float)) and duration < min_seconds:
            reason = "too_short"
        if reason:
            flagged.append(
                {
                    "reason": reason,
                    "sha256": str(sample.get("sha256") or "")[:16],
                    "source": source,
                    "path": path,
                    "duration_seconds": duration,
                }
            )

    return {
        "group_key": record.get("group_key"),
        "version_id": record.get("version_id"),
        "threshold": record.get("threshold"),
        "sample_count": len(samples),
        "duration_seconds": {"min": dmin, "max": dmax, "mean": dmean},
        "flagged": flagged,
        "outliers": _outliers(samples, outlier_threshold),
    }


def _outliers(samples: list[dict[str, Any]], threshold: float) -> list[dict[str, Any]]:
    """Rank samples by cosine similarity to the mean embedding (lowest first)."""
    vectors: list[list[float]] = []
    owners: list[dict[str, Any]] = []
    for sample in samples:
        embedding = sample.get("embedding")
        if isinstance(embedding, list) and len(embedding) == EMBEDDING_DIMENSION:
            vectors.append([float(x) for x in embedding])
            owners.append(sample)
    if len(vectors) < 3:
        return []

    mean = np.mean(np.asarray(vectors, dtype=float), axis=0)
    norm = float(np.linalg.norm(mean))
    if norm <= 0:
        return []
    mean = mean / norm

    scored: list[dict[str, Any]] = []
    matrix = np.asarray(vectors, dtype=float)
    norms = np.linalg.norm(matrix, axis=1)
    for sample, vector, vector_norm in zip(owners, matrix, norms, strict=True):
        similarity = float(np.dot(vector / vector_norm, mean)) if vector_norm > 0 else 0.0
        if similarity < threshold:
            scored.append(
                {
                    "similarity": similarity,
                    "sha256": str(sample.get("sha256") or "")[:16],
                    "source": str(sample.get("source") or ""),
                    "path": str(sample.get("path") or ""),
                }
            )
    scored.sort(key=lambda row: row["similarity"])
    return scored


def audit(config_path: str | None, *, users: list[str], options: argparse.Namespace) -> dict[str, Any]:
    config = load_config(config_path)
    registry = VoiceprintRegistry(Path(config.paths.artifacts_dir) / "results.sqlite", config)

    user_ids = users or registry.list_users()
    report: dict[str, Any] = {"artifacts_dir": config.paths.artifacts_dir, "users": {}}

    for user_id in user_ids:
        entry: dict[str, Any] = {"identity": None, "devices": {}}
        identity = registry.get(user_id)
        if identity:
            entry["identity"] = _scope_summary(
                identity,
                bad_sources=options.bad_source,
                bad_path_tokens=options.bad_path_token,
                outlier_threshold=options.outlier_threshold,
                min_seconds=options.min_sample_seconds,
            )
        for device_id, device_record in (registry.get_devices(user_id) or {}).items():
            entry["devices"][device_id] = _scope_summary(
                device_record,
                bad_sources=options.bad_source,
                bad_path_tokens=options.bad_path_token,
                outlier_threshold=options.outlier_threshold,
                min_seconds=options.min_sample_seconds,
            )
        report["users"][user_id] = entry

    report["totals"] = {
        "users": len(report["users"]),
        "flagged": sum(
            len(scope["flagged"])
            for user in report["users"].values()
            for scope in [user["identity"], *user["devices"].values()]
            if scope
        ),
        "outliers": sum(
            len(scope["outliers"])
            for user in report["users"].values()
            for scope in [user["identity"], *user["devices"].values()]
            if scope
        ),
    }
    return report


def render(report: dict[str, Any]) -> None:
    print("=" * 72)
    print("Voiceprint data audit (read-only)")
    print("=" * 72)
    for user_id, entry in report["users"].items():
        print(f"\n--- {user_id} ---")
        scopes = [("identity", entry["identity"]), *entry["devices"].items()]
        for label, scope in scopes:
            if not scope:
                print(f"  {label}: (no record)")
                continue
            duration = scope["duration_seconds"]
            mean = duration["mean"]
            print(
                f"  {label}: {scope['sample_count']} samples, "
                f"duration {'n/a' if mean is None else f'{mean:.1f}s mean'} "
                f"({duration['min']}-{duration['max']}s), threshold={scope['threshold']}"
            )
            for row in scope["flagged"]:
                print(f"      [!] {row['reason']}: {row['source'] or '-'} {row['sha256']}")
            for row in scope["outliers"]:
                print(f"      [~] similarity={row['similarity']:.3f} {row['source'] or '-'} {row['sha256']}")
            if not scope["flagged"] and not scope["outliers"]:
                print("      [ok] no flagged clips")
    print(f"\nflagged={report['totals']['flagged']} outliers={report['totals']['outliers']}")
    print("Nothing was modified. Purge decisions stay a human call.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit enrolled voiceprint data (read-only).")
    parser.add_argument("--config", default=None, help="Path to a YAML config file.")
    parser.add_argument("--user", action="append", default=[], help="Audit only this user_id (repeatable).")
    parser.add_argument(
        "--bad-source",
        action="append",
        default=[],
        help="Flag samples whose source contains this substring (repeatable).",
    )
    parser.add_argument(
        "--bad-path-token",
        action="append",
        default=[],
        help="Flag samples whose path contains this substring (repeatable).",
    )
    parser.add_argument("--outlier-threshold", type=float, default=0.5, help="Cosine outlier cutoff. Default: 0.5.")
    parser.add_argument("--min-sample-seconds", type=float, default=2.0, help="Too-short cutoff. Default: 2.0s.")
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    report = audit(options.config, users=options.user, options=options)
    if options.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        render(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
