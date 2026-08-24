#!/usr/bin/env python3
"""Clean up stale Scott identity and its versions."""
from neo4j import GraphDatabase
import os
from pathlib import Path

pwf = os.environ.get('NEO4J_PASSWORD_FILE', '')
pw = os.environ.get('NEO4J_PASSWORD', '') or (Path(pwf).read_text().strip() if pwf else '')
driver = GraphDatabase.driver(os.environ.get('NEO4J_URI', 'bolt://host.docker.internal:7687'), auth=(os.environ.get('NEO4J_USER', 'neo4j'), pw))

with driver.session(database=os.environ.get('NEO4J_DATABASE', 'neo4j')) as session:
    result = session.run(
        "MATCH (v:VoiceprintVersion) WHERE v.active <> true "
        "AND NOT EXISTS { MATCH (v)-[:HAS_SAMPLE]->() } "
        "DETACH DELETE v RETURN count(v) AS deleted"
    )
    print(f"Deleted inactive, sample-less versions: {result.single()['deleted']}")

    result = session.run(
        "MATCH (v:VoiceprintVersion) WHERE NOT (v)--() DETACH DELETE v RETURN count(v) AS deleted"
    )
    print(f"Deleted orphaned versions: {result.single()['deleted']}")

    session.run("MATCH (i:VoiceIdentity {user_id: 'Scott'}) DETACH DELETE i")
    print("Deleted Scott identity")

    session.run(
        "MATCH (g:VoiceprintGroup {group_key: 'Scott:identity'}) "
        "OPTIONAL MATCH (g)-[:ACTIVE_VERSION]->(v:VoiceprintVersion) "
        "SET v.user_id = 'scott' "
        "WITH g DETACH DELETE g"
    )
    print("Merged Scott:identity group")

    ids = list(session.run("MATCH (i:VoiceIdentity) RETURN i.user_id AS uid"))
    print(f"Identities: {[r['uid'] for r in ids]}")
    grps = list(session.run("MATCH (g:VoiceprintGroup) RETURN g.group_key AS gk"))
    print(f"Groups: {[r['gk'] for r in grps]}")
    total = session.run("MATCH (v:VoiceprintVersion) RETURN count(v) AS total").single()["total"]
    print(f"Total versions: {total}")
    total_s = session.run("MATCH (s:VoiceprintSample) RETURN count(s) AS total").single()["total"]
    print(f"Total samples: {total_s}")

driver.close()
