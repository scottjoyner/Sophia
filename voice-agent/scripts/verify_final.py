#!/usr/bin/env python3
"""Final verification of Neo4j voiceprint state."""
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
        MATCH (i:VoiceIdentity)
        OPTIONAL MATCH (i)-[:HAS_GROUP]->(g:VoiceprintGroup)
        OPTIONAL MATCH (g)-[:ACTIVE_VERSION]->(v:VoiceprintVersion)
        RETURN i.user_id AS uid, g.group_key AS gk,
               v.version_id AS vid, v.sample_count AS sc,
               v.embedding IS NOT NULL AS has_emb
    """))
    print("Final state:")
    for r in rows:
        print(f'  Identity: {r["uid"]}')
        print(f'    Group: {r["gk"]}')
        print(f'    Version: {str(r["vid"])[:16] if r["vid"] else "NONE"}')
        print(f'    Samples: {r["sc"]}')
        print(f'    Has embedding: {r["has_emb"]}')
    
    row = session.run('MATCH (v:VoiceprintVersion) RETURN count(v) AS total').single()
    print(f'Total versions: {row["total"]}')
