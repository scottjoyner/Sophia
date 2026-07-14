#!/usr/bin/env python3
"""Backfill global-speaker embeddings for all enrolled voiceprint identities.

Re-runs global-speaker linking for every enrolled ``VoiceIdentity`` so the
``speaker_embedding_idx`` is (re)populated and owner voiceprints bridge to the
global ``Speaker``/``GlobalSpeaker`` pool. Useful after a fresh database, a
schema change, or when global linkage is enabled post-enrollment.

Examples:

    python scripts/backfill_global_speaker_embeddings.py --config configs/dev.yaml
    NEO4J_PASSWORD=... python scripts/backfill_global_speaker_embeddings.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from voice_agent.config import load_config  # noqa: E402
from voice_agent.auth.registry import VoiceprintRegistry  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill global-speaker embeddings from enrolled voiceprints.")
    parser.add_argument("--config", default=None, help="Path to a YAML config file.")
    parser.add_argument("--artifacts-dir", default=None, help="Override artifacts dir (holds results.sqlite).")
    parser.add_argument("--match-threshold", type=float, default=0.85, help="Global-speaker link threshold.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a human summary.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(args.config)
    if args.artifacts_dir:
        config.paths = config.paths.model_copy(update={"artifacts_dir": args.artifacts_dir})
    if not config.neo4j.password:
        print("Neo4j password not configured (set NEO4J_PASSWORD). Aborting.", file=sys.stderr)
        return 2
    registry = VoiceprintRegistry(Path(config.paths.artifacts_dir) / "results.sqlite", config)
    if not registry.graph:
        print("Neo4j graph store unavailable. Aborting.", file=sys.stderr)
        return 2
    result = registry.backfill_global_speaker_embeddings(match_threshold=args.match_threshold)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"linked={result.get('linked', 0)} skipped={result.get('skipped', 0)} errors={result.get('errors', 0)}")
        for entry in result.get("users", []):
            status = entry.get("status")
            extra = ""
            if status == "ok":
                linkage = entry.get("linkage") or {}
                extra = f" -> {linkage.get('method')} ({linkage.get('matched_speaker_user_id') or linkage.get('global_speaker_id') or '?'})"
            elif status == "error":
                extra = f" -> {entry.get('error')}"
            print(f"  {entry.get('user_id')}: {status}{extra}")
    return 0 if result.get("errors", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
