#!/usr/bin/env python3
"""Consolidate duplicate VoiceprintGroup nodes without losing voiceprint data."""
from neo4j import GraphDatabase
import os
from pathlib import Path

pwf = os.environ.get('NEO4J_PASSWORD_FILE', '')
pw = os.environ.get('NEO4J_PASSWORD', '') or (Path(pwf).read_text().strip() if pwf else '')
driver = GraphDatabase.driver(os.environ.get('NEO4J_URI', 'bolt://host.docker.internal:7687'), auth=(os.environ.get('NEO4J_USER', 'neo4j'), pw))

with driver.session(database=os.environ.get('NEO4J_DATABASE', 'neo4j')) as session:
    # Step 1: Remove all ACTIVE_VERSION relationships from all VoiceprintGroup nodes
    session.run("MATCH (g:VoiceprintGroup)-[r:ACTIVE_VERSION]->(:VoiceprintVersion) DELETE r")
    print("Removed all ACTIVE_VERSION relationships")

    # Also remove HAS_VERSION relationships
    session.run("MATCH (g:VoiceprintGroup)-[r:HAS_VERSION]->(:VoiceprintVersion) DELETE r")
    print("Removed all HAS_VERSION relationships")

    # Step 2: Delete all duplicate VoiceprintGroup nodes, keeping one per group_key
    session.run(
        "MATCH (g:VoiceprintGroup) "
        "WITH g.group_key AS gk, collect(g) AS groups "
        "WHERE size(groups) > 1 "
        "UNWIND tail(groups) AS dup "
        "DETACH DELETE dup"
    )
    print("Deleted duplicate groups")

    # Step 3: Pick the best version for scott:identity (version with embedding)
    best_vid = session.run(
        "MATCH (v:VoiceprintVersion) "
        "WHERE v.embedding IS NOT NULL AND size(v.embedding) > 0 "
        "RETURN v.version_id AS vid, v.created_at AS created "
        "ORDER BY v.created_at DESC "
        "LIMIT 1"
    ).single()
    best_version_id = best_vid["vid"] if best_vid else None
    print(f"Best version: {best_version_id[:16] if best_version_id else 'NONE'}")

    # Also keep the second best (from Scott:identity)
    versions = list(session.run(
        "MATCH (v:VoiceprintVersion) "
        "WHERE v.embedding IS NOT NULL AND size(v.embedding) > 0 "
        "RETURN v.version_id AS vid, v.user_id AS uid, v.created_at AS created "
        "ORDER BY v.created_at DESC"
    ))
    print(f"All versions with embeddings: {len(versions)}")
    for v in versions:
        print(f"  vid={v['vid'][:16]} uid={v['uid']} created={v['created']}")

    # Step 4: Create ACTIVE_VERSION relationships
    # scott:identity -> best version (newest scott version)
    scott_version = None
    scott_identity_version = None
    for v in versions:
        uid = v["uid"] or ""
        if uid.lower() == "scott":
            scott_version = v["vid"]
            if scott_identity_version is None:
                scott_identity_version = v["vid"]

    if scott_identity_version:
        session.run(
            "MATCH (g:VoiceprintGroup {group_key: 'scott:identity'}) "
            "MATCH (v:VoiceprintVersion {version_id: $vid}) "
            "MERGE (g)-[:ACTIVE_VERSION]->(v) "
            "SET v.active = true",
            vid=scott_identity_version
        )
        print(f"Linked scott:identity -> {scott_identity_version[:16]}")

    # Scott:identity -> scott version (the most recent scott version)
    if scott_version and scott_version != scott_identity_version:
        session.run(
            "MATCH (g:VoiceprintGroup {group_key: 'Scott:identity'}) "
            "MATCH (v:VoiceprintVersion {version_id: $vid}) "
            "MERGE (g)-[:ACTIVE_VERSION]->(v) "
            "SET v.active = true",
            vid=scott_version
        )
        print(f"Linked Scott:identity -> {scott_version[:16]}")

    # If there's still a Scott:identity group and we have a best version, merge it
    scott_group = list(session.run(
        "MATCH (g:VoiceprintGroup {group_key: 'Scott:identity'}) "
        "RETURN g.user_id AS uid"
    ))
    if scott_group:
        print(f"Scott:identity group exists (user={scott_group[0]['uid']})")

    # Final state
    identities = list(session.run("MATCH (i:VoiceIdentity) RETURN i.user_id AS uid"))
    print(f"\nRemaining identities: {[r['uid'] for r in identities]}")

    groups = list(session.run(
        "MATCH (g:VoiceprintGroup) "
        "RETURN g.group_key AS gk, g.user_id AS uid, "
        "       [(g)-[:ACTIVE_VERSION]->(v) | v.version_id][0] AS active_vid"
    ))
    for g in groups:
        print(f"  group={g['gk']} uid={g['uid']} active_vid={str(g['active_vid'])[:16] if g['active_vid'] else 'NONE'}")

    total_v = session.run("MATCH (v:VoiceprintVersion) RETURN count(v) AS total").single()["total"]
    print(f"Total VoiceprintVersion nodes: {total_v}")

driver.close()
