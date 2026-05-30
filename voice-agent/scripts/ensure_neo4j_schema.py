#!/usr/bin/env python3
"""Install Sophia Neo4j memory graph constraints.

Run this before relying on concurrent offline capture sync:

    python scripts/ensure_neo4j_schema.py --config configs/dev.yaml
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

from voice_agent.auth.neo4j_schema import ensure_sophia_neo4j_schema  # noqa: E402
from voice_agent.config import load_config  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install Sophia Neo4j schema constraints.")
    parser.add_argument("--config", help="Optional Sophia YAML config path.")
    parser.add_argument("--json", action="store_true", help="Emit compact JSON only.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    result = ensure_sophia_neo4j_schema(
        config.neo4j.uri,
        config.neo4j.user,
        config.neo4j.password or "",
        database=config.neo4j.database,
    )
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print("Sophia Neo4j schema install")
        print(f"  ok: {result.get('ok')}")
        print(f"  applied: {result.get('applied', 0)}")
        if result.get("error"):
            print(f"  error: {result['error']}")
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
