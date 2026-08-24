#!/usr/bin/env python3
"""Final verification of Neo4j state."""
from neo4j import GraphDatabase
import os
from pathlib import Path

pwf = os.environ.get('NEO4J_PASSWORD_FILE', '')
pw = os.environ.get('NEO4J_PASSWORD', '') or (Path(pwf).read_text().strip() if pwf else '')
driver = GraphDatabase.driver(os.environ.get('NEO4J_URI', 'bolt://host.docker.internal:7687'), auth=(os.environ.get('NEO4J_USER', 'neo4j'), pw))

with driver.session(database=os.environ.get('NEO4J_DATABASE', 'neo4j')) as session:
    rows = list(session.run(
        "MATCH (i:VoiceIdentity)-[:HAS_GROUP]->(g:VoiceprintGroup)-[:ACTIVE_VERSION]->(v:VoiceprintVersion) "
        "OPTIONAL MATCH (v)-[:HAS_SAMPLE]->(s:VoiceprintSample) "
        "RETURN i.user_id AS uid, g.group_key AS gk, "
        "       v.version_id AS vid, v.sample_count AS sc, "
        "       count(s) AS sample_nodes, v.source AS src, "
        "       v.embedding IS NOT NULL AS has_emb"
    ))
    for r in rows:
        print(f"User: {r['uid']}")
        print(f"  Group: {r['gk']}")
        print(f"  Version: {r['vid'][:16]}")
        print(f"  Samples (prop): {r['sc']}")
        print(f"  Sample nodes: {r['sample_nodes']}")
        print(f"  Has embedding: {r['has_emb']}")
        print(f"  Source: {r['src']}")
    
    total = session.run("MATCH (v:VoiceprintVersion) RETURN count(v) AS total").single()["total"]
    print(f"Total versions: {total}")
    total_s = session.run("MATCH (s:VoiceprintSample) RETURN count(s) AS total").single()["total"]
    print(f"Total VoiceprintSample nodes: {total_s}")
    
    identities = list(session.run("MATCH (i:VoiceIdentity) RETURN i.user_id AS uid"))
    print(f"Identities: {[r['uid'] for r in identities]}")

driver.close()
