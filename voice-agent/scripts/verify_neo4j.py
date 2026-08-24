#!/usr/bin/env python3
"""Verify Neo4j state."""
from neo4j import GraphDatabase
import os
from pathlib import Path

password_file = os.environ.get('NEO4J_PASSWORD_FILE', '')
password = os.environ.get('NEO4J_PASSWORD', '') or (Path(password_file).read_text().strip() if password_file else '')

driver = GraphDatabase.driver(
    os.environ.get('NEO4J_URI', 'bolt://host.docker.internal:7687'),
    auth=(os.environ.get('NEO4J_USER', 'neo4j'), password)
)

with driver.session(database=os.environ.get('NEO4J_DATABASE', 'neo4j')) as session:
    identities = list(session.run(
        "MATCH (i:VoiceIdentity) OPTIONAL MATCH (i)-[:HAS_GROUP]->(g) "
        "RETURN i.user_id AS uid, collect(g.group_key) AS groups"
    ))
    for r in identities:
        print(f"Identity: {r['uid']} groups={r['groups']}")
    
    active = list(session.run(
        "MATCH (g:VoiceprintGroup)-[:ACTIVE_VERSION]->(v:VoiceprintVersion) "
        "RETURN g.group_key AS gk, v.version_id AS vid, "
        "v.sample_count AS sc, v.source AS src, v.active AS act"
    ))
    for v in active:
        print(f"Active: group={v['gk']} version={v['vid'][:16]} samples={v['sc']} source={v['src']}")
    
    total = session.run("MATCH (v:VoiceprintVersion) RETURN count(v) AS total").single()["total"]
    print(f"Total VoiceprintVersion nodes: {total}")

driver.close()
