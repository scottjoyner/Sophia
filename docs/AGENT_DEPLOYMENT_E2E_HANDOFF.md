# Sophia Agent Deployment and E2E Handoff

This runbook is for a follow-on agent or developer taking over deployment and end-to-end validation of the Sophia voice-agent reliability updates.

## Goal

Deploy the hardening updates, validate the offline-first mobile capture path, and prove that Neo4j remains the durable memory brain while browser/server SQLite layers remain temporary retry state.

## Architecture roles

| Layer | Role |
| --- | --- |
| Browser IndexedDB | Temporary offline capture queue on the phone/browser |
| Server SQLite capture idempotency | Temporary retry response cache keyed by `client_capture_id` |
| Server SQLite graph outbox | Temporary retry journal for Neo4j writes |
| Filesystem | Accepted raw audio artifacts |
| Neo4j | Durable Sophia memory brain |

## Required local services

Minimum local validation can run without Neo4j by using mock/default config and focused tests. Full E2E requires:

- Python 3.11+
- Node 20+
- npm
- Neo4j reachable from the voice-agent process
- Chrome/Chromium for Playwright tests

Environment values are documented in:

```text
voice-agent/.env.example
```

Critical Neo4j variables:

```bash
export NEO4J_URI=bolt://127.0.0.1:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD='<local-password>'
export NEO4J_DATABASE=memory
```

Do not commit real secrets.

## Fresh clone setup

From repo root:

```bash
cd voice-agent
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

For browser tests:

```bash
cd voice-agent
npm install
npx playwright install chromium
```

## Apply hardening patches

From repo root:

```bash
python voice-agent/scripts/apply_hardening_patches.py
```

This applies, in order:

1. app reliability / upload limits / graph outbox
2. UI session refresh
3. request hardening headers and request IDs
4. rate limits
5. offline browser queue
6. offline storage-risk controls
7. UI offline diagnostics panel
8. capture idempotency
9. graph capture reconciliation
10. offline diagnostics endpoint

## Verify patch state

```bash
python voice-agent/scripts/verify_hardening.py
python voice-agent/scripts/check_patch_pipeline.py
```

`check_patch_pipeline.py` runs the patch sequence against a temporary copy of `app.py` and verifies the result without modifying the working tree.

## Install Neo4j schema constraints

From `voice-agent`:

```bash
python scripts/ensure_neo4j_schema.py --config configs/dev.yaml
```

If there is no config file, rely on environment variables and run:

```bash
python scripts/ensure_neo4j_schema.py
```

Constraints must exist before concurrent offline sync is trusted. They protect:

- `SophiaCapture.dedupe_key`
- `SophiaCapture.capture_id`
- `Transcript.id`
- `Speaker.user_id`
- `Audio.path`
- `Device.device_id`
- `Meeting.id`
- `MeetingSegment.id`

## Run focused Python hardening tests

From `voice-agent`:

```bash
pytest \
  tests/test_apply_hardening_patches.py \
  tests/test_verify_hardening.py \
  tests/test_request_hardening.py \
  tests/test_request_hardening_patch.py \
  tests/test_rate_limits.py \
  tests/test_rate_limit_patch.py \
  tests/test_upload_limits.py \
  tests/test_trusted_sessions.py \
  tests/test_graph_outbox.py \
  tests/test_replay_graph_outbox_script.py \
  tests/test_capture_idempotency.py \
  tests/test_capture_idempotency_patch.py \
  tests/test_graph_capture_reconciliation_patch.py \
  tests/test_neo4j_capture_idempotency.py \
  tests/test_neo4j_schema.py \
  tests/test_offline_browser_queue_patch.py \
  tests/test_offline_storage_risk_patch.py \
  tests/test_offline_diagnostics_patch.py \
  tests/test_app_reliability_patch.py \
  tests/test_ui_session_refresh_patch.py
```

## Run browser smoke tests

From `voice-agent`:

```bash
npm run test:ui -- --project=chromium
```

The current Playwright smoke test validates the offline diagnostics panel against mocked diagnostics responses.

## Start app for manual E2E

From `voice-agent`, after environment variables are set:

```bash
python -m voice_agent.cli server
```

If the CLI entrypoint differs in the deployed branch, inspect:

```bash
python -m voice_agent.cli --help
```

Open:

```text
http://127.0.0.1:8765
```

## Manual E2E checklist

### 1. Readiness and diagnostics

Check endpoints:

```bash
curl http://127.0.0.1:8765/healthz
curl http://127.0.0.1:8765/readyz
curl http://127.0.0.1:8765/diagnostics/offline
curl http://127.0.0.1:8765/graph/outbox/status
```

Expected:

- `/healthz` returns OK.
- `/readyz` reports config/backend state.
- `/diagnostics/offline` includes readiness, graph outbox, idempotency, and storage-role fields.
- `/graph/outbox/status` includes `pending_total`, `due`, and `healthy`.

### 2. Browser UI diagnostics

In the browser UI:

- Confirm top status pills render.
- Confirm `offline queue` pill renders.
- Confirm `memory sync` pill renders.
- Confirm `Reliability Diagnostics` panel renders.
- Click `Refresh Diagnostics`.
- Confirm diagnostics JSON appears.

### 3. Offline recording preservation

In browser devtools or phone airplane mode:

1. Disable network.
2. Create a short recording or transcript-only capture.
3. Save it.
4. Confirm UI says it was saved locally.
5. Refresh the page.
6. Confirm the offline queue item remains.

Expected:

- IndexedDB contains the queued recording.
- UI does not imply it is in Neo4j yet.

### 4. Online retry

1. Restore network.
2. Click `Retry Sync`, or wait for the foreground sync loop.
3. Confirm item moves through `reconciling` / `uploading`.
4. Confirm final state is one of:
   - `synced_to_neo4j`
   - `server_graph_pending`
   - `uploaded_to_sidecar`

Expected:

- If Neo4j is available, the item should become `synced_to_neo4j`.
- If Neo4j is down, it should show graph pending and be visible in graph outbox status.

### 5. Idempotent retry

Use the same queued item or simulate a dropped response:

1. Upload once.
2. Re-run sync for the same `client_capture_id`.
3. Call:

```bash
curl http://127.0.0.1:8765/capture/by-client-id/<client_capture_id>
```

Expected:

- Existing capture is returned.
- No duplicate `SophiaCapture` node is created.
- Response source may be `idempotency_cache` or `neo4j`.

### 6. Graph catch-up reconciliation

With Neo4j restored after outage:

1. Replay graph outbox:

```bash
python scripts/replay_graph_outbox.py
```

2. In the UI, click retry/refresh diagnostics.
3. Confirm pending browser items reconcile from `server_graph_pending` to synced once `/capture/by-client-id/{client_capture_id}` finds Neo4j.

## Acceptance criteria

Deployment is acceptable when:

- Hardening patches apply cleanly.
- Hardening verifier passes.
- Disposable patch pipeline passes.
- Focused Python tests pass.
- Playwright diagnostics smoke test passes.
- Neo4j schema constraints install successfully.
- Offline capture survives page refresh while disconnected.
- Retry does not duplicate captures.
- Graph-pending state is visible and can reconcile after Neo4j catches up.
- UI diagnostics clearly shows local/server/Neo4j status.

## Known follow-up work

- Split inline UI into templates/static JS/CSS.
- Add a service worker and optional Background Sync.
- Add richer Playwright tests for actual IndexedDB queue operations.
- Add optional WebCrypto encryption for local browser audio blobs.
- Add a production deployment profile for Docker/systemd.
