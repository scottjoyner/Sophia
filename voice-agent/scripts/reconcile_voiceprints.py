#!/usr/bin/env python3
"""Reconcile locally-stored voiceprints (SQLite) into Neo4j.

Keeps the Neo4j voiceprint graph eventually consistent when enrollments were
accepted while Neo4j was temporarily unavailable. SQLite is the source of truth
for the embedding; this pushes it into the graph (VoiceprintVersion + global
Speaker linkage) when missing, skipping records that already match.

Examples:

    python scripts/reconcile_voiceprints.py --config configs/container.yaml --once
    python scripts/reconcile_voiceprints.py --artifacts-dir /data --interval-seconds 30 --json
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from voice_agent.auth.registry import VoiceprintRegistry  # noqa: E402
from voice_agent.config import load_config  # noqa: E402

_STOP = False


def _request_stop(signum, frame) -> None:  # noqa: ARG001
    global _STOP
    _STOP = True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reconcile SQLite voiceprints into Neo4j.")
    parser.add_argument("--config", default=None, help="Path to a YAML config file.")
    parser.add_argument("--artifacts-dir", default=None, help="Override artifacts dir (holds results.sqlite).")
    parser.add_argument("--admin-key", default="", help="Owner override key (required by the API-equivalent flow).")
    parser.add_argument("--force", action="store_true", help="Re-push even when the graph already matches.")
    parser.add_argument("--interval-seconds", type=float, default=30.0, help="Loop interval. Default: 30s.")
    parser.add_argument("--once", action="store_true", help="Run one pass then exit.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a human summary.")
    return parser


def reconcile_once(config, *, force: bool) -> dict:
    if not config.neo4j.password:
        print("Neo4j password not configured (set NEO4J_PASSWORD). Aborting.", file=sys.stderr)
        return {"ok": False, "error": "Neo4j not configured"}
    registry = VoiceprintRegistry(Path(config.paths.artifacts_dir) / "results.sqlite", config)
    if not registry.graph:
        return {"ok": False, "error": "Neo4j graph store unavailable"}
    return registry.reconcile_to_neo4j(force=force)


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(args.config)
    if args.artifacts_dir:
        config.paths = config.paths.model_copy(update={"artifacts_dir": args.artifacts_dir})

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    if args.once:
        result = reconcile_once(config, force=args.force)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"synced={result.get('synced', 0)} skipped={result.get('skipped', 0)} errors={result.get('errors', 0)}")
        return 0 if result.get("ok") else 1

    while not _STOP:
        result = reconcile_once(config, force=args.force)
        if args.json:
            print(json.dumps(result, default=str))
        else:
            print(f"synced={result.get('synced', 0)} skipped={result.get('skipped', 0)} errors={result.get('errors', 0)}")
        for _ in range(int(args.interval_seconds * 10)):
            if _STOP:
                break
            time.sleep(0.1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
