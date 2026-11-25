#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VOICE_AGENT="$ROOT/voice-agent"
RUN_BROWSER_TESTS="${RUN_BROWSER_TESTS:-0}"
RUN_PHASE8="${RUN_PHASE8:-0}"
INSTALL_NEO4J_SCHEMA="${INSTALL_NEO4J_SCHEMA:-0}"
PHASE8_OUT="${PHASE8_OUT:-$VOICE_AGENT/reports/phase8}"
BASE_URL="${BASE_URL:-http://127.0.0.1:8765}"
HARDENING_STATUS=0
PHASE8_STATUS=0
BROWSER_STATUS=0
# Marker drift is reported loudly but does not fail the run unless asked: the
# markers track source text that refactors legitimately move.
STRICT_HARDENING="${STRICT_HARDENING:-0}"

cd "$VOICE_AGENT"

echo "== Sophia local E2E validation =="
echo "Repo: $ROOT"

# Marker presence is not a gate on its own: the markers drift as the code is
# refactored, so a failure here is reported and counted instead of being swallowed.
printf '\n== Verify hardening markers ==\n'
if python scripts/verify_hardening.py; then
  echo "hardening markers: all present"
else
  echo "hardening markers: MISSING (see above) -- marker drift, not a test failure"
  HARDENING_STATUS=1
fi

printf '\n== Run Python test suite ==\n'
# PYTEST_BASETEMP keeps pytest's tmp_path fixtures off /tmp when that matters.
PYTEST_ARGS=()
if [[ -n "${PYTEST_BASETEMP:-}" ]]; then
  PYTEST_ARGS+=(--basetemp="$PYTEST_BASETEMP")
fi
pytest "${PYTEST_ARGS[@]}" \
  tests/test_verify_hardening.py \
  tests/test_request_hardening.py \
  tests/test_rate_limits.py \
  tests/test_upload_limits.py \
  tests/test_trusted_sessions.py \
  tests/test_graph_outbox.py \
  tests/test_replay_graph_outbox_script.py \
  tests/test_capture_idempotency.py \
  tests/test_neo4j_capture_idempotency.py \
  tests/test_neo4j_schema.py \
  tests/test_phase8_validation.py

if [[ "$INSTALL_NEO4J_SCHEMA" == "1" ]]; then
  printf '\n== Install Neo4j schema constraints ==\n'
  python scripts/ensure_neo4j_schema.py
else
  printf '\n== Skipping Neo4j schema install ==\n'
  echo "Set INSTALL_NEO4J_SCHEMA=1 after NEO4J_URI/USER/PASSWORD/DATABASE are configured."
fi

# Phase 8 is real-world only: every item skips itself with the exact blocker when
# its prerequisites are missing. Skips are NOT failures here (exit 0), but they are
# printed, so nobody can mistake this run for real-world evidence.
if [[ "$RUN_PHASE8" == "1" ]]; then
  printf '\n== Phase 8 real-world validation (items skip when prerequisites are missing) ==\n'
  if python scripts/phase8_validation.py --out-dir "$PHASE8_OUT" --base-url "$BASE_URL"; then
    echo "phase8: no item failed (items that skipped are still unproven -- see $PHASE8_OUT/phase8_report.json)"
  else
    PHASE8_STATUS=1
    echo "phase8: at least one item FAILED -- see $PHASE8_OUT/phase8_report.json"
  fi
else
  printf '\n== Skipping Phase 8 real-world validation ==\n'
  echo "Set RUN_PHASE8=1 to run it (it needs real speech, real models and real credentials)."
fi

if [[ "$RUN_BROWSER_TESTS" == "1" ]]; then
  printf '\n== Run Playwright browser smoke tests ==\n'
  if [[ ! -d node_modules ]]; then
    npm install
  fi
  # Browser downloads are never automatic: an offline host, or someone who does
  # not want a ~300 MB download, must opt in explicitly.
  if [[ "${INSTALL_BROWSERS:-0}" == "1" ]]; then
    npx playwright install chromium
  fi
  if npm run test:ui -- --project=chromium; then
    echo "browser smoke: passed"
  else
    BROWSER_STATUS=1
    echo "browser smoke: failed or chromium is missing."
    echo "Install the browser deliberately with: INSTALL_BROWSERS=1 $0   (or: npx playwright install chromium)"
  fi
else
  printf '\n== Skipping browser tests ==\n'
  echo "Set RUN_BROWSER_TESTS=1 to run Playwright smoke tests (and INSTALL_BROWSERS=1 to allow the download)."
fi

printf "\nSophia local E2E validation completed (hardening_markers=$HARDENING_STATUS phase8=$PHASE8_STATUS browser=$BROWSER_STATUS).\n"
if [[ "$PHASE8_STATUS" != "0" || "$BROWSER_STATUS" != "0" || ( "$HARDENING_STATUS" != "0" && "$STRICT_HARDENING" == "1" ) ]]; then
  echo "Some stages reported problems (see above)."
  exit 1
fi