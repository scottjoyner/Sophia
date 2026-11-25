#!/usr/bin/env python3
"""Dump the Neo4j voiceprint graph as text or JSON (read-only).

One inspector for "what does the voiceprint graph currently hold?". It answers
that question without writing anything: no schema bootstrap, no MERGE, no
DETACH DELETE, so it is safe to run against production.

Reports per user: identity nodes, their groups, every version with its
active flag / sample count / embedding dimension, and the sample nodes behind
each version. Duplicate identity casing (Scott vs scott) and groups with no
active version are called out, since both are what voiceprint consolidation
bugs leave behind.

Credentials come from load_config(), so NEO4J_PASSWORD_FILE / config
neo4j.password_file are honoured exactly as the service resolves them.

Examples:

    python scripts/inspect_neo4j_voiceprints.py --config configs/container.yaml
    python scripts/inspect_neo4j_voiceprints.py --user scott --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from voice_agent.config import load_config  # noqa: E402

IDENTITY_QUERY = """
MATCH (i:VoiceIdentity)
OPTIONAL MATCH (i)-[:HAS_GROUP]->(g:VoiceprintGroup)
RETURN i.user_id AS user_id, collect(g.group_key) AS group_keys
ORDER BY i.user_id
"""

VERSION_QUERY = """
MATCH (g:VoiceprintGroup)
OPTIONAL MATCH (g)-[r]->(v:VoiceprintVersion)
RETURN g.group_key AS group_key, g.user_id AS user_id, type(r) AS rel,
       v.version_id AS version_id, v.sample_count AS sample_count,
       v.source AS source, v.created_at AS created_at, v.active AS active,
       size(v.embedding) AS embedding_dim
ORDER BY group_key, created_at
"""

SAMPLE_QUERY = """
MATCH (v:VoiceprintVersion)<-[:HAS_SAMPLE]-(s:VoiceprintSample)
RETURN v.version_id AS version_id, count(s) AS sample_nodes
ORDER BY version_id
"""

ORPHAN_QUERY = """
MATCH (v:VoiceprintVersion)
WHERE NOT (v)--()
RETURN count(v) AS count
"""


def inspect(config_path: str | None, *, users: list[str]) -> dict[str, Any]:
    from neo4j import GraphDatabase

    config = load_config(config_path)
    neo4j = config.neo4j
    if not neo4j.password:
        raise SystemExit(
            "No Neo4j password configured. Set NEO4J_PASSWORD or NEO4J_PASSWORD_FILE "
            "(or neo4j.password_file in the config)."
        )

    driver = GraphDatabase.driver(neo4j.uri, auth=(neo4j.user, neo4j.password))
    try:
        with driver.session(database=neo4j.database) as session:
            identities = [dict(row) for row in session.run(IDENTITY_QUERY)]
            versions = [dict(row) for row in session.run(VERSION_QUERY)]
            sample_nodes = {row["version_id"]: row["sample_nodes"] for row in session.run(SAMPLE_QUERY)}
            orphan_versions = session.run(ORPHAN_QUERY).single()["count"]
    finally:
        driver.close()

    wanted = set(users)
    if wanted:
        identities = [row for row in identities if row["user_id"] in wanted]
        keep = {key for row in identities for key in row["group_keys"]}
        versions = [row for row in versions if row["group_key"] in keep]
        visible = {row["version_id"] for row in versions}
        sample_nodes = {vid: count for vid, count in sample_nodes.items() if vid in visible}

    active_by_group: dict[str, int] = {}
    for row in versions:
        if row["rel"] == "ACTIVE_VERSION":
            active_by_group[row["group_key"]] = active_by_group.get(row["group_key"], 0) + 1

    lower_cased = {}
    for row in identities:
        lower_cased.setdefault(str(row["user_id"]).lower(), []).append(row["user_id"])

    return {
        "neo4j": {"uri": neo4j.uri, "database": neo4j.database, "user": neo4j.user},
        "identities": identities,
        "versions": [
            {**row, "sample_nodes": sample_nodes.get(row["version_id"])}
            for row in versions
        ],
        "groups_without_active_version": sorted(
            group_key for group_key, count in active_by_group.items() if count == 0
        ),
        "duplicate_identity_casing": {
            key: sorted(values) for key, values in lower_cased.items() if len(values) > 1
        },
        "orphan_version_count": orphan_versions,
    }


def render(report: dict[str, Any]) -> None:
    print("=" * 72)
    print("Neo4j voiceprint graph (read-only)")
    print(f"uri={report['neo4j']['uri']} database={report['neo4j']['database']}")
    print("=" * 72)
    for row in report["identities"]:
        print(f"  {row['user_id']}: groups={row['group_keys'] or []}")
    if not report["identities"]:
        print("  (no VoiceIdentity nodes)")
    print("\nversions:")
    for row in report["versions"]:
        print(
            f"  {row['group_key'] or '-'} via {row['rel'] or '-'} "
            f"vid={str(row['version_id'])[:16]} samples={row['sample_count']} "
            f"sample_nodes={row['sample_nodes']} emb_dim={row['embedding_dim']} "
            f"active={row['active']} source={row['source']}"
        )
    print(f"\norphan versions (no relationships): {report['orphan_version_count']}")
    if report["groups_without_active_version"]:
        print(f"[!] groups with no ACTIVE_VERSION: {report['groups_without_active_version']}")
    if report["duplicate_identity_casing"]:
        print(f"[!] duplicate identity casing: {report['duplicate_identity_casing']}")
    print("Nothing was modified.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect the Neo4j voiceprint graph (read-only).")
    parser.add_argument("--config", default=None, help="Path to a YAML config file.")
    parser.add_argument("--user", action="append", default=[], help="Restrict to this user_id (repeatable).")
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    report = inspect(options.config, users=options.user)
    if options.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        render(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
