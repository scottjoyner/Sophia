#!/usr/bin/env bash
set -euo pipefail

SOURCE="${SOURCE:-/media/scott/NAS1/fileserver/audio}"
DEST="${DEST:-/mnt/S/sophia-ingest/audio}"
MANIFEST="${MANIFEST:-/mnt/S/sophia-ingest/manifests/staged-audio.jsonl}"
NEO4J_URI="${NEO4J_URI:-bolt://localhost:7687}"
NEO4J_USER="${NEO4J_USER:-neo4j}"
NEO4J_DATABASE="${NEO4J_DATABASE:-memory}"
NEO4J_PASSWORD="${NEO4J_PASSWORD:?set NEO4J_PASSWORD}"
LIMIT_ARGS=()

if [[ "${LIMIT:-0}" != "0" ]]; then
  LIMIT_ARGS+=(--limit "${LIMIT}")
fi

python3 "$(dirname "$0")/stage_nas_audio_to_ssd.py" \
  --source "$SOURCE" \
  --dest "$DEST" \
  --manifest "$MANIFEST" \
  "${LIMIT_ARGS[@]}"

python3 "$(dirname "$0")/register_staged_audio.py" \
  --manifest "$MANIFEST" \
  --uri "$NEO4J_URI" \
  --user "$NEO4J_USER" \
  --password "$NEO4J_PASSWORD" \
  --database "$NEO4J_DATABASE"
