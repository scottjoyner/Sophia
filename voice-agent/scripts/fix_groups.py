#!/usr/bin/env python3
"""Fix duplicate scott:identity group."""
from neo4j import GraphDatabase
import os
from pathlib import Path

pwf = os.environ.get('NEO4J_PASSWORD_FILE', '')
pw = os.environ.get('NEO4J_PASSWORD', '') or (Path(pwf).read_text().strip() if pwf else '')
driver = GraphDatabase.driver(os.environ.get('NEO4J_URI', 'bolt://host.docker.internal:7687'), auth=(os.environ.get('NEO4J_USER', 'neo4j'), pw))

with driver.session(database=os.environ.get('NEO4J_DATABASE', 'neo4j')) as session:
    result = session.run(
        "MATCH (g:VoiceprintGroup {group_key: 'scott:identity'}) "
        "WHERE g.user_id IS NULL OR g.user_id = '' "
        "OPTIONAL MATCH (g)-[:ACTIVE_VERSION]->(v:VoiceprintVersion) "
        "FOREACH (_ IN CASE WHEN v IS NULL THEN [] ELSE [1] END | "
        "  SET v.active = false "
        ") "
        "DETACH DELETE g "
        "RETURN count(g) AS deleted"
    )
    print(f"Deleted orphaned groups: {result.single()['deleted']}")

    groups = list(session.run(
        "MATCH (g:VoiceprintGroup {group_key: 'scott:identity'}) "
        "RETURN g.user_id AS uid, "
        "size([(g)-[:ACTIVE_VERSION]->() | 1]) AS active_count"
    ))
    for g in groups:
        print(f"  Group user={g['uid']} active_versions={g['active_count']}")

    total = session.run("MATCH (v:VoiceprintVersion) RETURN count(v) AS total").single()["total"]
    print(f"Total versions: {total}")

    identities = list(session.run("MATCH (i:VoiceIdentity) RETURN i.user_id AS uid"))
    print(f"Identities: {[r['uid'] for r in identities]}")

driver.close()
