# Next Agent Prompt: Sophia Deployment and E2E Validation

You are taking over the Sophia repository after a reliability hardening pass focused on mobile/offline capture, Neo4j-first memory, and diagnostics.

Repository:

```text
https://github.com/scottjoyner/Sophia
```

## Mission

Deploy and validate the voice-agent hardening updates end-to-end. Do not add new feature scope until the reliability path is proven.

## Primary docs to read first

1. `docs/AGENT_DEPLOYMENT_E2E_HANDOFF.md`
2. `docs/OFFLINE_FIRST_BROWSER_CAPTURE_DESIGN.md`
3. `voice-agent/.env.example`

## Required architecture stance

Neo4j is the durable Sophia memory brain.

Temporary state layers:

- Browser IndexedDB: offline capture queue
- Server SQLite capture idempotency: retry response cache
- Server SQLite graph outbox: Neo4j write retry journal
- Filesystem: accepted raw audio artifacts

Do not treat SQLite or browser storage as long-term memory.

## First commands

From repo root:

```bash
cd voice-agent
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cd ..
python voice-agent/scripts/run_local_e2e_validation.sh
```

For browser tests:

```bash
cd voice-agent
npm install
npx playwright install chromium
RUN_BROWSER_TESTS=1 scripts/run_local_e2e_validation.sh
```

For Neo4j schema install, set environment variables first:

```bash
export NEO4J_URI=bolt://127.0.0.1:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD='<local-password>'
export NEO4J_DATABASE=memory
INSTALL_NEO4J_SCHEMA=1 voice-agent/scripts/run_local_e2e_validation.sh
```

Do not commit real secrets.

## Expected hardening patch sequence

The orchestrator owns the patch order:

```bash
python voice-agent/scripts/apply_hardening_patches.py
```

Then verify:

```bash
python voice-agent/scripts/verify_hardening.py
python voice-agent/scripts/check_patch_pipeline.py
```

## E2E success criteria

The deployment is not complete until all of these are true:

1. Hardening patches apply cleanly.
2. `verify_hardening.py` passes.
3. `check_patch_pipeline.py` passes.
4. Focused Python hardening tests pass.
5. Playwright smoke test passes when browser dependencies are installed.
6. Neo4j constraints install successfully in the target environment.
7. `/diagnostics/offline` returns readiness, graph outbox, idempotency, and role data.
8. UI Reliability Diagnostics panel renders and refreshes.
9. Offline capture survives a page refresh while disconnected.
10. Online retry uploads or reconciles without duplicate `SophiaCapture` nodes.
11. Graph-pending captures reconcile after graph outbox replay and Neo4j availability returns.

## Manual endpoint checks

With the app running:

```bash
curl http://127.0.0.1:8765/healthz
curl http://127.0.0.1:8765/readyz
curl http://127.0.0.1:8765/diagnostics/offline
curl http://127.0.0.1:8765/graph/outbox/status
```

Use `/capture/by-client-id/{client_capture_id}` to reconcile uncertain browser uploads.

## Failure handling

If a patch fails:

1. Run `python voice-agent/scripts/check_patch_pipeline.py --keep-temp`.
2. Inspect the temporary patched `app.py`.
3. Fix the smallest patcher possible.
4. Add or update a focused test.
5. Re-run local validation.

If Playwright fails:

1. Confirm the app is serving the patched UI.
2. Confirm `/diagnostics/offline` responds.
3. Confirm mocked route behavior if running against Playwright-only smoke tests.
4. Add a screenshot or trace artifact in CI if needed.

If Neo4j writes duplicate captures:

1. Confirm constraints from `scripts/ensure_neo4j_schema.py` are installed.
2. Confirm `client_capture_id` reaches `/capture`.
3. Confirm `SophiaCapture.dedupe_key` is present.
4. Confirm `/capture/by-client-id/{client_capture_id}` returns one capture.

## Next implementation priorities after validation

Only after E2E passes:

1. Split inline UI into templates/static JS/CSS.
2. Add service worker and optional Background Sync.
3. Add richer Playwright tests for IndexedDB offline queue operations.
4. Add optional WebCrypto encryption for local audio blobs.
5. Add a production deployment profile for Docker/systemd.
