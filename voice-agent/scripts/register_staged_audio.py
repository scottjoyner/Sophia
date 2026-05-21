#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _records(manifest: Path):
    with manifest.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"{manifest}:{line_no}: bad JSON: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Register SSD-staged audio files in Neo4j memory database.")
    parser.add_argument("--manifest", default="/mnt/S/sophia-ingest/manifests/staged-audio.jsonl")
    parser.add_argument("--uri", default="bolt://localhost:7687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", required=True)
    parser.add_argument("--database", default="memory")
    parser.add_argument("--batch-id", default="")
    parser.add_argument("--host-prefix", default="/mnt/S/sophia-ingest")
    parser.add_argument("--container-prefix", default="/ssd-ingest")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    manifest = Path(args.manifest)
    if not manifest.exists():
        print(f"manifest not found: {manifest}", file=sys.stderr)
        return 2

    rows = []
    for record in _records(manifest):
        if args.limit and len(rows) >= args.limit:
            break
        staged_path = str(record.get("staged_path", ""))
        container_path = str(record.get("container_path", ""))
        if not container_path and staged_path.startswith(args.host_prefix.rstrip("/") + "/"):
            container_path = args.container_prefix.rstrip("/") + staged_path[len(args.host_prefix.rstrip("/")) :]
        rows.append(
            {
                "path": staged_path,
                "container_path": container_path,
                "original_path": record.get("source_path", ""),
                "relative_path": record.get("relative_path", ""),
                "size": int(record.get("size") or 0),
                "mtime": int(record.get("mtime") or 0),
                "extension": record.get("extension", ""),
                "sha256": record.get("sha256", ""),
            }
        )
    if args.dry_run:
        print(json.dumps({"would_register": len(rows), "sample": rows[:5]}, indent=2))
        return 0
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:
        raise RuntimeError("neo4j package is required: pip install neo4j") from exc

    query = """
    UNWIND $rows AS row
    MERGE (file:AudioFile {path: row.path})
      ON CREATE SET file.created_at = datetime()
    SET file.original_path = row.original_path,
        file.container_path = row.container_path,
        file.relative_path = row.relative_path,
        file.size = row.size,
        file.mtime = row.mtime,
        file.extension = row.extension,
        file.sha256 = row.sha256,
        file.storage_tier = 'ssd_staging',
        file.ingest_status = coalesce(file.ingest_status, 'pending'),
        file.source = 'sophia_ssd_staging',
        file.updated_at = datetime()
    WITH file
    MERGE (batch:AutoIngestBatch {batch_id: $batch_id})
      ON CREATE SET batch.created_at = datetime(),
                    batch.source = 'sophia_ssd_staging'
    MERGE (batch)-[:CONTAINS]->(file)
    RETURN count(file) AS registered
    """
    batch_id = args.batch_id or f"ssd-stage:{manifest.name}"
    driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))
    with driver.session(database=args.database) as session:
        result = session.run(query, rows=rows, batch_id=batch_id).single()
    driver.close()
    print(json.dumps({"registered": result["registered"] if result else 0, "batch_id": batch_id}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
