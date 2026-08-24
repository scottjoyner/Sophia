#!/usr/bin/env python3
"""Clean up stale VoiceprintVersion nodes in Neo4j."""
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
    row = session.run("""
        MATCH (g:VoiceprintGroup {group_key: 'scott:identity'})-[:ACTIVE_VERSION]->(v:VoiceprintVersion)
        RETURN v.version_id AS vid
    """).single()
    active_vid = row['vid'] if row else None
    print(f'Active version: {active_vid[:16] if active_vid else "NONE"}')
    
    if active_vid:
        stale = list(session.run("""
            MATCH (g:VoiceprintGroup {group_key: 'scott:identity'})-[:HAS_VERSION]->(v:VoiceprintVersion)
            WHERE v.version_id <> $active_vid
            RETURN v.version_id AS vid
        """, active_vid=active_vid))
        print(f'Found {len(stale)} stale versions')
        
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
        print(f'Deleted {deleted} stale versions')
        
        row = session.run("""
            MATCH (g:VoiceprintGroup {group_key: 'scott:identity'})-[:HAS_VERSION]->(v:VoiceprintVersion)
            RETURN count(v) AS remaining
        """).single()
        print(f'Remaining versions: {row["remaining"]}')

driver.close()
