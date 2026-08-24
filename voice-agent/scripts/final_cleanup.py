#!/usr/bin/env python3
"""Final cleanup of stale Neo4j data."""
from neo4j import GraphDatabase
import os
from pathlib import Path

pwf = os.environ.get('NEO4J_PASSWORD_FILE', '')
pw = os.environ.get('NEO4J_PASSWORD', '') or (Path(pwf).read_text().strip() if pwf else '')
driver = GraphDatabase.driver(os.environ.get('NEO4J_URI', 'bolt://host.docker.internal:7687'), auth=(os.environ.get('NEO4J_USER', 'neo4j'), pw))

with driver.session(database=os.environ.get('NEO4J_DATABASE', 'neo4j')) as session:
    active = list(session.run(
        "MATCH (g:VoiceprintGroup)-[:ACTIVE_VERSION]->(v:VoiceprintVersion) "
        "RETURN v.version_id AS vid"
    ))
    keep = {r["vid"] for r in active}
    print(f"Active versions to keep: {keep}")

    stale = list(session.run(
        "MATCH (v:VoiceprintVersion) "
        "WHERE NOT v.version_id IN $keep "
        "RETURN v.version_id AS vid",
        keep=list(keep)
    ))
    print(f"Stale versions to delete: {len(stale)}")

    batch, deleted = [], 0
    for r in stale:
        batch.append(r["vid"])
        if len(batch) >= 200:
            session.run(
                "MATCH (v:VoiceprintVersion) WHERE v.version_id IN $batch DETACH DELETE v",
                batch=batch
            )
            deleted += len(batch)
            batch = []
    if batch:
        session.run(
            "MATCH (v:VoiceprintVersion) WHERE v.version_id IN $batch DETACH DELETE v",
            batch=batch
        )
        deleted += len(batch)
    print(f"Deleted {deleted} stale versions")

    # Merge Scott -> scott
    session.run(
        "MATCH (dup:VoiceIdentity {user_id: 'Scott'}) "
        "OPTIONAL MATCH (dup)-[:HAS_GROUP]->(g:VoiceprintGroup) "
        "FOREACH (_ IN CASE WHEN g IS NULL THEN [] ELSE [1] END | "
        "  SET g.user_id = 'scott'"
        ") "
        "WITH dup "
        "DETACH DELETE dup"
    )
    print("Merged Scott -> scott")

    # Merge Scott:identity group into scott:identity
    session.run(
        "MATCH (old:VoiceprintGroup {group_key: 'Scott:identity'}) "
        "OPTIONAL MATCH (old)-[:ACTIVE_VERSION]->(v:VoiceprintVersion) "
        "FOREACH (_ IN CASE WHEN v IS NULL THEN [] ELSE [1] END | "
        "  MERGE (canon:VoiceprintGroup {group_key: 'scott:identity'})-[:ACTIVE_VERSION]->(v) "
        ") "
        "WITH old "
        "DETACH DELETE old"
    )
    print("Merged Scott:identity group")

    identities = list(session.run("MATCH (i:VoiceIdentity) RETURN i.user_id AS uid"))
    print(f"Remaining identities: {[r['uid'] for r in identities]}")
    groups = list(session.run("MATCH (g:VoiceprintGroup) RETURN g.group_key AS gk"))
    print(f"Remaining groups: {[r['gk'] for r in groups]}")
    total = session.run("MATCH (v:VoiceprintVersion) RETURN count(v) AS total").single()["total"]
    print(f"Total versions: {total}")

driver.close()
