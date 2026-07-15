#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VOICE_AGENT="$ROOT/voice-agent"
RUN_BROWSER_TESTS="${RUN_BROWSER_TESTS:-0}"
INSTALL_NEO4J_SCHEMA="${INSTALL_NEO4J_SCHEMA:-0}"

cd "$VOICE_AGENT"

echo "== Sophia local E2E validation =="
echo "Repo: $ROOT"

echo "\n== Verify hardening markers =="
python scripts/verify_hardening.py || true

echo "\n== Run Python test suite =="
pytest \
  tests/test_verify_hardening.py \
  tests/test_request_hardening.py \
  tests/test_rate_limits.py \
  tests/test_upload_limits.py \
  tests/test_trusted_sessions.py \
  tests/test_graph_outbox.py \
  tests/test_replay_graph_outbox_script.py \
  tests/test_capture_idempotency.py \
  tests/test_neo4j_capture_idempotency.py \
  tests/test_neo4j_schema.py

if [[ "$INSTALL_NEO4J_SCHEMA" == "1" ]]; then
  echo "\n== Install Neo4j schema constraints =="
  python scripts/ensure_neo4j_schema.py
else
  echo "\n== Skipping Neo4j schema install =="
  echo "Set INSTALL_NEO4J_SCHEMA=1 after NEO4J_URI/USER/PASSWORD/DATABASE are configured."
fi

if [[ "$RUN_BROWSER_TESTS" == "1" ]]; then
  echo "\n== Run Playwright browser smoke tests =="
  if [[ ! -d node_modules ]]; then
    npm install
  fi
  npx playwright install chromium
  npm run test:ui -- --project=chromium
else
  echo "\n== Skipping browser tests =="
  echo "Set RUN_BROWSER_TESTS=1 to run Playwright smoke tests."
fi

echo "\nSophia local E2E validation completed."
