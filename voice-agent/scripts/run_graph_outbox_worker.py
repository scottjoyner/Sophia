#!/usr/bin/env python3
"""Continuously replay Sophia graph outbox writes into Neo4j.

This worker keeps the Neo4j memory layer eventually consistent when captures are
accepted while Neo4j is temporarily unavailable. SQLite remains only the local
outbox/retry journal; successful replay writes the durable memory into Neo4j and
marks the outbox item succeeded.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path

# Support running directly from the repository checkout without installing the package.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from voice_agent.config import load_config  # noqa: E402
from voice_agent.server.graph_outbox import GraphOutbox, replay_graph_outbox_items  # noqa: E402

_STOP = False


def _request_stop(signum, frame):  # noqa: ARG001
    global _STOP
    _STOP = True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Sophia graph outbox replay worker.")
    parser.add_argument("--config", help="Optional Sophia YAML config path.")
    parser.add_argument(
        "--artifacts-dir",
        help="Override artifacts directory containing graph_outbox.sqlite. Defaults to config.paths.artifacts_dir.",
    )
    parser.add_argument("--limit", type=int, default=25, help="Maximum due outbox items to replay each loop. Default: 25.")
    parser.add_argument("--interval-seconds", type=float, default=30.0, help="Replay loop interval. Default: 30 seconds.")
    parser.add_argument("--once", action="store_true", help="Run one replay pass then exit.")
    parser.add_argument("--json", action="store_true", help="Emit JSON lines instead of human-readable logs.")
    parser.add_argument(
        "--prune-succeeded-days",
        type=float,
        default=7.0,
        help="Delete succeeded outbox rows older than this many days. Default: 7. Use 0 to prune all succeeded rows.",
    )
    return parser


def replay_once(config, outbox: GraphOutbox, *, limit: int, prune_succeeded_days: float = 7.0) -> dict:
    before = outbox.summary()
    result = replay_graph_outbox_items(
        outbox,
        neo4j_uri=config.neo4j.uri,
        neo4j_user=config.neo4j.user,
        neo4j_password=config.neo4j.password or "",
        neo4j_database=config.neo4j.database,
        limit=max(1, min(limit, 500)),
    )
    pruned = outbox.prune_succeeded(older_than_ms=int(max(0.0, prune_succeeded_days) * 24 * 60 * 60 * 1000))
    after = outbox.summary()
    return {
        "ts_ms": int(time.time() * 1000),
        "ok": result.get("ok", False),
        "before": before,
        "result": result,
        "pruned_succeeded": pruned,
        "after": after,
    }


def emit(payload: dict, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, sort_keys=True), flush=True)
    else:
        print(
            "graph-outbox replay "
            f"ok={payload.get('ok')} before={payload.get('before')} "
            f"result={payload.get('result')} pruned={payload.get('pruned_succeeded')} after={payload.get('after')}",
            flush=True,
        )


def main(argv: list[str] | None = None) -> int:
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    artifacts_dir = Path(args.artifacts_dir or config.paths.artifacts_dir)
    outbox = GraphOutbox(artifacts_dir / "graph_outbox.sqlite")

    exit_code = 0
    while not _STOP:
        payload = replay_once(config, outbox, limit=args.limit, prune_succeeded_days=args.prune_succeeded_days)
        emit(payload, as_json=args.json)
        if not payload["ok"]:
            exit_code = 2
        if args.once:
            return exit_code
        sleep_for = max(1.0, args.interval_seconds)
        deadline = time.monotonic() + sleep_for
        while not _STOP and time.monotonic() < deadline:
            time.sleep(min(0.5, deadline - time.monotonic()))

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
