#!/usr/bin/env python3
"""Final cleanup: merge all into single identity/group with one active version."""
from neo4j import GraphDatabase
import os
from pathlib import Path

pwf = os.environ.get('NEO4J_PASSWORD_FILE', '')
pw = os.environ.get('NEO4J_PASSWORD', '') or (Path(pwf).read_text().strip() if pwf else '')
driver = GraphDatabase.driver(os.environ.get('NEO4J_URI', 'bolt://host.docker.internal:7687'), auth=(os.environ.get('NEO4J_USER', 'neo4j'), pw))

with driver.session(database=os.environ.get('NEO4J_DATABASE', 'neo4j')) as session:
    # Step 1: Merge Scott:identity group's active version into scott:identity
    # First, get the Scott:identity active version
    row = session.run(
        "MATCH (g:VoiceprintGroup {group_key: 'Scott:identity'})-[:ACTIVE_VERSION]->(v:VoiceprintVersion) "
        "RETURN v.version_id AS vid"
    ).single()
    scott_vid = row['vid'] if row else None
    print(f"Scott:identity active version: {scott_vid[:16] if scott_vid else 'NONE'}")

    row = session.run(
        "MATCH (g:VoiceprintGroup {group_key: 'scott:identity'})-[:ACTIVE_VERSION]->(v:VoiceprintVersion) "
        "RETURN v.version_id AS vid"
    ).single()
    canon_vid = row['vid'] if row else None
    print(f"scott:identity active version: {canon_vid[:16] if canon_vid else 'NONE'}")

    # Step 2: Pick the best version (the one with samples)
    best_vid = None
    for vid_to_check in [canon_vid, scott_vid]:
        if vid_to_check:
            row = session.run(
                "MATCH (v:VoiceprintVersion {version_id: $vid}) "
                "RETURN v.sample_count AS sc, v.embedding IS NOT NULL AS has_emb",
                vid=vid_to_check
            ).single()
            if row and row['has_emb'] and row['sc'] > 0:
                best_vid = vid_to_check
                break
    if best_vid is None:
        best_vid = canon_vid or scott_vid
    print(f"Best version: {best_vid[:16] if best_vid else 'NONE'}")

    # Step 3: Remove all ACTIVE_VERSION relationships
    session.run("MATCH (g:VoiceprintGroup)-[r:ACTIVE_VERSION]->(:VoiceprintVersion) DELETE r")
    print("Removed all ACTIVE_VERSION relationships")

    # Step 4: Delete Scott:identity group
    session.run(
        "MATCH (g:VoiceprintGroup {group_key: 'Scott:identity'}) "
        "OPTIONAL MATCH (g)-[:HAS_VERSION]->(v:VoiceprintVersion) "
        "DETACH DELETE g"
    )
    print("Deleted Scott:identity group")

    # Step 5: Set the best version as active for scott:identity
    if best_vid:
        session.run(
            "MATCH (v:VoiceprintVersion {version_id: $vid}) "
            "SET v.active = true",
            vid=best_vid
        )
        session.run(
            "MATCH (g:VoiceprintGroup {group_key: 'scott:identity'}) "
            "MATCH (v:VoiceprintVersion {version_id: $vid}) "
            "MERGE (g)-[:ACTIVE_VERSION]->(v)",
            vid=best_vid
        )
        print(f"Set {best_vid[:16]} as active version")

    # Step 6: Deactivate all other versions
    session.run(
        "MATCH (v:VoiceprintVersion) WHERE v.version_id <> $vid "
        "SET v.active = false",
        vid=best_vid or ""
    )
    print("Deactivated all other versions")

    # Step 7: Delete all identies except scott
    session.run("MATCH (i:VoiceIdentity) WHERE i.user_id <> 'scott' DETACH DELETE i")
    print("Deleted stale identities")

    # Final state
    ids = list(session.run("MATCH (i:VoiceIdentity) RETURN i.user_id AS uid"))
    print(f"Identities: {[r['uid'] for r in ids]}")
    grps = list(session.run("MATCH (g:VoiceprintGroup) RETURN g.group_key AS gk, g.user_id AS uid"))
    print(f"Groups: {[(r['gk'], r['uid']) for r in grps]}")
    versions = list(session.run(
        "MATCH (g:VoiceprintGroup)-[:ACTIVE_VERSION]->(v:VoiceprintVersion) "
        "RETURN v.version_id AS vid, v.sample_count AS sc, v.active AS act, "
        "       v.embedding IS NOT NULL AS has_emb"
    ))
    for v in versions:
        print(f"  Active: vid={v['vid'][:16]} samples={v['sc']} active={v['act']} has_emb={v['has_emb']}")
    total = session.run("MATCH (v:VoiceprintVersion) RETURN count(v) AS total").single()["total"]
    print(f"Total versions: {total}")
    total_s = session.run("MATCH (s:VoiceprintSample) RETURN count(s) AS total").single()["total"]
    print(f"Total samples: {total_s}")

driver.close()
