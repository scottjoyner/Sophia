#!/usr/bin/env python3
"""Verify Neo4j voiceprint state after cleanup."""
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
    rows = list(session.run("""
        MATCH (i:VoiceIdentity)-[:HAS_GROUP]->(g:VoiceprintGroup)-[:ACTIVE_VERSION]->(v:VoiceprintVersion)
        OPTIONAL MATCH (v)-[:HAS_SAMPLE]->(s:VoiceprintSample)
        RETURN i.user_id AS uid, g.group_key AS gk,
               v.version_id AS vid, v.sample_count AS sc,
               v.embedding IS NOT NULL AS has_emb,
               count(s) AS sample_count
    """))
    for r in rows:
        print(f'User: {r["uid"]}')
        print(f'  Group: {r["gk"]}')
        print(f'  Version: {r["vid"][:16]}')
        print(f'  Has embedding: {r["has_emb"]}')
        print(f'  Sample count (prop): {r["sc"]}')
        print(f'  Sample nodes: {r["sample_count"]}')
    
    row = session.run('MATCH (v:VoiceprintVersion) RETURN count(v) AS total').single()
    print(f'Total VoiceprintVersion nodes in DB: {row["total"]}')
