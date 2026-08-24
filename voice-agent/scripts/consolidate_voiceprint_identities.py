#!/usr/bin/env python3
"""Consolidate duplicate voiceprint identities and re-push sample metadata.

This script:
1. Finds duplicate VoiceIdentity nodes (case-insensitive) in Neo4j
2. Merges duplicate identities into the canonical (lowercase) user_id
3. Re-runs reconcile to push proper per-sample metadata from SQLite
4. Removes stale/empty identities after merge

Usage:
    python scripts/consolidate_voiceprint_identities.py --config configs/container.yaml [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from voice_agent.auth.registry import VoiceprintRegistry
from voice_agent.config import load_config


def consolidate(config_path: str, dry_run: bool = True):
    config = load_config(config_path)
    registry = VoiceprintRegistry(Path(config.paths.artifacts_dir) / "results.sqlite", config)

    if not registry.graph:
        print("Neo4j not configured — cannot consolidate graph identities.")
        return

    driver = registry.graph._driver()
    try:
        with driver.session(database=registry.graph.database) as session:
            # Step 1: Find all VoiceIdentity nodes
            rows = list(session.run("MATCH (i:VoiceIdentity) RETURN i.user_id AS uid ORDER BY i.user_id"))
            uids = [row["uid"] for row in rows]
            print(f"Found {len(uids)} VoiceIdentity nodes: {uids}")

            # Group by lowercase
            groups: dict[str, list[str]] = {}
            for uid in uids:
                key = uid.lower()
                groups.setdefault(key, []).append(uid)

            duplicates = {k: v for k, v in groups.items() if len(v) > 1}
            if not duplicates:
                print("No duplicate identities found.")
            else:
                print(f"\nDuplicate groups: {len(duplicates)}")
                for canon, variants in duplicates.items():
                    print(f"  Canonical: {canon} -> variants: {variants}")
                    if not dry_run:
                        primary = canon
                        for variant in variants:
                            if variant == primary:
                                continue
                            print(f"    Merging {variant} -> {primary}")
                            # Re-parent all groups from the duplicate to the canonical
                            session.run(
                                """
                                MATCH (dup:VoiceIdentity {user_id: $variant})
                                MATCH (canon:VoiceIdentity {user_id: $primary})
                                OPTIONAL MATCH (dup)-[r:HAS_GROUP]->(g:VoiceprintGroup)
                                FOREACH (_ IN CASE WHEN r IS NULL THEN [] ELSE [1] END |
                                    SET g.user_id = $primary
                                )
                                WITH dup, canon
                                OPTIONAL MATCH (dup)-[r:IS_SPEAKER]->(s:Speaker)
                                FOREACH (_ IN CASE WHEN r IS NULL THEN [] ELSE [1] END |
                                    MERGE (canon)-[:IS_SPEAKER]->(s)
                                )
                                WITH dup, canon
                                OPTIONAL MATCH (dup)-[r:IS_GLOBAL_SPEAKER]->(gs)
                                FOREACH (_ IN CASE WHEN r IS NULL THEN [] ELSE [1] END |
                                    MERGE (canon)-[:IS_GLOBAL_SPEAKER]->(gs)
                                )
                                WITH dup
                                DETACH DELETE dup
                                """,
                                variant=variant,
                                primary=primary,
                            )
                            print(f"    Deleted duplicate: {variant}")

            # Step 2: Check for groups with 0 samples that have embeddings
            print("\n--- Checking for groups with 0 samples ---")
            groups = list(session.run("""
                MATCH (g:VoiceprintGroup)
                OPTIONAL MATCH (g)-[:ACTIVE_VERSION]->(v:VoiceprintVersion)
                RETURN g.group_key AS group_key, g.user_id AS uid,
                       v.version_id AS vid, v.sample_count AS sample_count,
                       v.samples_json AS samples_json
                ORDER BY g.group_key
            """))
            for g in groups:
                sample_count = g.get("sample_count") or 0
                if sample_count == 0:
                    print(f"  Group {g['group_key']} (user={g['uid']}): 0 samples — may need re-enrollment")

            # Step 3: Run reconcile to push SQLite data back to Neo4j
            if not dry_run:
                print("\n--- Running reconcile ---")
                result = registry.reconcile_to_neo4j(source="consolidate", force=True)
                print(f"  Synced: {result['synced']}, Skipped: {result['skipped']}, Drift: {result['drift']}, Graph-only: {result['graph_only']}")
                for u in result.get("users", []):
                    print(f"  User {u['user_id']}: synced={u['synced']} skipped={u['skipped']} drift={u['drift']} graph_only={u['graph_only']}")

            print("\nDone.")

    finally:
        driver.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Consolidate duplicate voiceprint identities")
    parser.add_argument("--config", default="configs/container.yaml", help="Config file path")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Only report, don't mutate")
    parser.add_argument("--apply", action="store_true", help="Actually apply changes")
    args = parser.parse_args()
    if args.apply:
        args.dry_run = False
    consolidate(args.config, dry_run=args.dry_run)
