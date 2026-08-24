#!/usr/bin/env python3
"""Full cleanup of stale voiceprint data in Neo4j."""
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
    # Step 1: Merge Scott -> scott identity
    print("=== Step 1: Merge Scott -> scott ===")
    result = session.run("""
        MATCH (dup:VoiceIdentity {user_id: 'Scott'})
        MATCH (canon:VoiceIdentity {user_id: 'scott'})
        OPTIONAL MATCH (dup)-[:HAS_GROUP]->(g:VoiceprintGroup)
        FOREACH (_ IN CASE WHEN g IS NULL THEN [] ELSE [1] END |
            SET g.user_id = 'scott'
        )
        WITH dup, canon
        OPTIONAL MATCH (dup)-[:IS_SPEAKER]->(s:Speaker)
        FOREACH (_ IN CASE WHEN s IS NULL THEN [] ELSE [1] END |
            MERGE (canon)-[:IS_SPEAKER]->(s)
        )
        WITH dup, canon
        OPTIONAL MATCH (dup)-[:IS_GLOBAL_SPEAKER]->(gs)
        FOREACH (_ IN CASE WHEN gs IS NULL THEN [] ELSE [1] END |
            MERGE (canon)-[:IS_GLOBAL_SPEAKER]->(gs)
        )
        WITH dup
        DETACH DELETE dup
    """)
    print("Merged Scott -> scott")
    
    # Now merge the Scott:identity group into scott:identity
    session.run("""
        MATCH (old:VoiceprintGroup {group_key: 'Scott:identity'})
        MATCH (canon:VoiceprintGroup {group_key: 'scott:identity'})
        OPTIONAL MATCH (old)-[:ACTIVE_VERSION]->(v:VoiceprintVersion)
        FOREACH (_ IN CASE WHEN v IS NULL THEN [] ELSE [1] END |
            MERGE (canon)-[:ACTIVE_VERSION]->(v)
        )
        WITH old
        DETACH DELETE old
    """)
    print("Merged Scott:identity group into scott:identity")
    
    # Now clean up all versions not linked to any group
    stale = list(session.run("""
        MATCH (v:VoiceprintVersion)
        WHERE NOT (v)<-[:HAS_VERSION]-()
        RETURN v.version_id AS vid
    """))
    print(f'Found {len(stale)} unlinked versions')
    
    batch = []
    deleted = 0
    for r in stale:
        batch.append(r['vid'])
        if len(batch) >= 200:
            session.run("""
                MATCH (v:VoiceprintVersion)
                WHERE v.version_id IN $batch
                DETACH DELETE v
            """, batch=batch)
            deleted += len(batch)
            batch = []
    if batch:
        session.run("""
            MATCH (v:VoiceprintVersion)
            WHERE v.version_id IN $batch
            DETACH DELETE v
        """, batch=batch)
        deleted += len(batch)
    print(f'Deleted {deleted} unlinked versions')
    
    # Final count
    row = session.run('MATCH (v:VoiceprintVersion) RETURN count(v) AS total').single()
    print(f'Total VoiceprintVersion nodes remaining: {row["total"]}')
