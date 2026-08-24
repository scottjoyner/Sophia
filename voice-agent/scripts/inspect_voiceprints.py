#!/usr/bin/env python3
"""Inspect all voiceprint versions without modifying anything."""
from neo4j import GraphDatabase
import os
from pathlib import Path

pwf = os.environ.get('NEO4J_PASSWORD_FILE', '')
pw = os.environ.get('NEO4J_PASSWORD', '') or (Path(pwf).read_text().strip() if pwf else '')
driver = GraphDatabase.driver(os.environ.get('NEO4J_URI', 'bolt://host.docker.internal:7687'), auth=(os.environ.get('NEO4J_USER', 'neo4j'), pw))

with driver.session(database=os.environ.get('NEO4J_DATABASE', 'neo4j')) as session:
    versions = list(session.run(
        "MATCH (g:VoiceprintGroup {group_key: 'scott:identity'})-[:ACTIVE_VERSION]->(v:VoiceprintVersion) "
        "RETURN id(v) AS nid, v.version_id AS vid, v.sample_count AS sc, "
        "v.source AS src, v.created_at AS created, "
        "size(v.embedding) AS emb_len, "
        "v.embedding[0..5] AS emb_head, "
        "v.user_id AS uid "
        "ORDER BY v.created_at"
    ))
    print(f"Active versions for scott:identity: {len(versions)}")
    for v in versions:
        emb_head = v['emb_head']
        non_zero = sum(1 for x in emb_head if x and abs(x) > 0.001) if emb_head else 0
        print(f"  nid={v['nid']} vid={v['vid'][:16]} samples={v['sc']} "
              f"source={v['src']} emb_len={v['emb_len']} "
              f"emb_head_nonzero={non_zero}/{len(emb_head) if emb_head else 0} "
              f"user_id={v['uid']}")

    # Also check what groups exist
    groups = list(session.run(
        "MATCH (g:VoiceprintGroup) "
        "OPTIONAL MATCH (g)-[:ACTIVE_VERSION]->(v:VoiceprintVersion) "
        "RETURN g.group_key AS gk, g.user_id AS uid, "
        "v.version_id AS vid, v.embedding IS NOT NULL AS has_emb "
        "ORDER BY gk"
    ))
    print(f"\nAll groups: {len(groups)}")
    for g in groups:
        print(f"  gk={g['gk']} uid={g['uid']} "
              f"vid={str(g['vid'])[:16] if g['vid'] else 'NONE'} "
              f"has_emb={g['has_emb']}")

driver.close()
