#!/usr/bin/env python3
"""Replay pending Sophia graph outbox writes into Neo4j.

Neo4j is Sophia's durable memory.  This script drains the local SQLite outbox
that is used only when Neo4j was temporarily unavailable at capture time.

Examples:

    python scripts/replay_graph_outbox.py --config configs/dev.yaml
    python scripts/replay_graph_outbox.py --artifacts-dir /data --limit 100 --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Support running directly from the repository checkout without installing the package.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from voice_agent.config import load_config  # noqa: E402
from voice_agent.server.graph_outbox import (  # noqa: E402
    OUTBOX_DB_NAME,
    GraphOutbox,
    replay_graph_outbox_items,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay pending Sophia graph outbox writes into Neo4j.")
    parser.add_argument("--config", help="Optional Sophia YAML config path.")
    parser.add_argument(
        "--artifacts-dir",
        help="Override artifacts directory containing results.sqlite. Defaults to config.paths.artifacts_dir.",
    )
    parser.add_argument("--limit", type=int, default=25, help="Maximum due outbox items to replay. Default: 25.")
    parser.add_argument("--json", action="store_true", help="Emit compact JSON only.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    artifacts_dir = Path(args.artifacts_dir or config.paths.artifacts_dir)
    # Must match the server's wiring in app.py, which builds the outbox over the
    # shared artifacts database (results.sqlite) rather than a dedicated file.
    # Pointing at any other name silently replays an empty database and leaves
    # the real backlog untouched.
    db_path = artifacts_dir / OUTBOX_DB_NAME
    if not db_path.exists():
        print(
            f"graph outbox not found at {db_path}; nothing to replay "
            "(the server writes the outbox into the artifacts database)",
            file=sys.stderr,
        )
        return 1
    outbox = GraphOutbox(db_path)

    before = outbox.counts()
    result = replay_graph_outbox_items(
        outbox,
        neo4j_uri=config.neo4j.uri,
        neo4j_user=config.neo4j.user,
        neo4j_password=config.neo4j.password or "",
        neo4j_database=config.neo4j.database,
        limit=max(1, min(args.limit, 500)),
    )
    after = outbox.counts()
    payload = {"ok": result.get("ok", False), "before": before, "result": result, "after": after}

    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print("Sophia graph outbox replay")
        print(f"  artifacts_dir: {artifacts_dir}")
        print(f"  before: {before}")
        print(f"  result: {result}")
        print(f"  after: {after}")

    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
