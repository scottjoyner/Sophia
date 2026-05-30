# Sophia Reliability Hardening Plan

## Purpose

Sophia is moving from a useful local voice sidecar into a user-facing system. The current implementation already has the right core pieces: FastAPI HTTP/WebSocket service, voice auth, capture persistence, meeting processing, Neo4j integration, dispatch to AssistX, health endpoints, and a mobile-friendly UI. The next pass should focus on making the app predictable under browser refreshes, container restarts, bad audio inputs, network drops, and partial dependency outages.

This document defines the reliability work that should happen after the first UI auth-session refresh patch.

## Current Reliability Concerns

### 1. Client-side auth cache is useful but not enough

The UI patch should reduce repeated auto-verification by caching a short trusted browser session. That improves UX, but production reliability needs a server-side auth session model too.

Current risk:

- Browser localStorage can be stale, cleared, copied, or inconsistent across devices.
- The server does not currently expose a first-class `/session/status` contract.
- Auth state is not durable across server restarts except through voiceprint data.

Target behavior:

- Browser can optimistically display cached auth.
- Server remains the source of truth for trusted session validity.
- Manual verify always re-checks voice and refreshes server/client session state.

### 2. Meeting tasks are process-local only

`MeetingTaskManager` stores task state in an in-memory dictionary. This is fine for dev, but it is not reliable for long-running jobs.

Current risk:

- Container restart loses all in-flight task status.
- Multiple workers would not share task state.
- Completed task records can accumulate in memory.
- Large uploads are read into memory before background processing begins.

Target behavior:

- Task metadata should be persisted to SQLite or Neo4j.
- Task manager should enforce a max retained task count and TTL.
- Uploads should stream to disk with size limits before queueing.
- UI should recover task status after refresh using a task id.

### 3. Healthcheck only verifies liveness

Docker currently checks `/healthz`, which only proves the HTTP process answers. It does not prove artifacts, voice DB, model readiness, Neo4j write readiness, or LLM/dispatch dependency state.

Target behavior:

- `/healthz`: process liveness only.
- `/readyz`: local filesystem, registry DB, model/config readiness.
- `/status`: full detail for UI/debug.
- Docker healthcheck should use `/readyz` once readiness becomes strict enough.

### 4. Admin enrollment token should not be committed in compose

The compose file currently sets an owner override token directly in environment. That is unsafe for a user-facing deployment.

Target behavior:

- `SOPHIA_OWNER_OVERRIDE_TOKEN` should come from `.env`, Docker secret, or a local ignored config file.
- Example compose should show the variable but not include a real token.
- Owner override endpoint should be rate-limited and audited.

### 5. Upload validation needs hard limits

Current upload endpoints accept audio and meeting files without an obvious shared upload policy.

Target behavior:

- Max file size by endpoint.
- Allowed content types/extensions.
- Minimum/maximum audio duration after decode.
- Clear 4xx errors for user mistakes.
- Temporary files always cleaned up.
- ffmpeg errors preserved in sanitized form for debug logs.

### 6. Frontend has silent failure paths

Several UI refresh functions catch errors quietly or only update a local status label. That makes the app feel frozen when a dependency fails.

Target behavior:

- All refresh calls update a visible state: connected, degraded, offline, auth needed, processing, retrying.
- Debug panel records failures.
- Retry/backoff behavior is consistent.
- Top status icons refresh from one shared `refreshTopStatus()` loop.

### 7. Graph and event writes need backpressure

Neo4j writes happen synchronously in request paths. Event logging failures are swallowed into events, which is helpful, but request latency and graph outage behavior need tighter boundaries.

Target behavior:

- Graph writes use short timeouts.
- Graph write failures never block core capture/verify UX longer than a defined threshold.
- Failed graph writes are queued for retry when possible.
- UI clearly distinguishes `saved locally` vs `saved to graph`.

## P0 Reliability Work

### P0.1 Add server-side trusted session state

Create a small session store in SQLite under artifacts, e.g. `/data/runs/results.sqlite` or a dedicated `/data/sophia_sessions.sqlite`.

Schema:

```sql
CREATE TABLE IF NOT EXISTS trusted_voice_sessions (
  session_key TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  device_id TEXT,
  device_fingerprint TEXT,
  score REAL NOT NULL,
  accepted INTEGER NOT NULL,
  match_source TEXT,
  voiceprint_version_id TEXT,
  created_at_ms INTEGER NOT NULL,
  expires_at_ms INTEGER NOT NULL,
  last_seen_ms INTEGER NOT NULL
);
```

Endpoints:

```http
GET /session/status?user_id=scott&session_id=mobile&device_id=...
POST /session/clear
```

Acceptance criteria:

- Successful `/auth/verify` writes a trusted session when accepted.
- UI loads trusted session from server first, then falls back to local cache.
- Expired sessions are rejected and cleaned up.
- Manual `Verify` refreshes the session.
- `Forget trusted session` clears both localStorage and server session.

### P0.2 Remove committed owner override secret

Change compose from:

```yaml
SOPHIA_OWNER_OVERRIDE_TOKEN: "...real token..."
```

to:

```yaml
SOPHIA_OWNER_OVERRIDE_TOKEN: ${SOPHIA_OWNER_OVERRIDE_TOKEN:-}
```

Add `.env.example` with placeholder values only.

Acceptance criteria:

- No real owner override token appears in committed files.
- App returns a clear 503 when owner override is enabled but no token is configured.
- README documents local-only setup.

### P0.3 Add shared upload guardrails

Create a helper similar to:

```python
async def save_upload_with_limits(upload, prefix, max_bytes, allowed_suffixes): ...
```

Apply it to:

- `/auth/verify`
- `/capture`
- `/voiceprints/enroll`
- `/voiceprints/owner-override-enroll`
- `/meeting/process`

Initial limits:

- verify: 15 MB
- capture: 50 MB
- voiceprint enroll: 50 MB
- meeting: 500 MB local-only, lower if exposed remotely

Acceptance criteria:

- Oversized uploads return HTTP 413.
- Unsupported extensions return HTTP 415 or 422.
- Empty files return HTTP 400.
- Temporary files are removed on failure.

### P0.4 Make readiness meaningful

Expand `/readyz` to include:

- artifacts directory exists and writable
- voiceprint registry can open
- capture directory exists/writable
- configured protocol is valid
- ffmpeg is available when upload conversion is enabled
- optional Neo4j write readiness field

Acceptance criteria:

- `/healthz` stays cheap and always local.
- `/readyz` fails only when the service cannot perform core local work.
- Docker healthcheck can safely move to `/readyz` after this is stable.

## P1 Reliability Work

### P1.1 Persist meeting task state

Move `MeetingTaskManager` to a SQLite-backed task store.

Fields:

- task_id
- status
- step
- progress_pct
- result_json
- error
- created_at_ms
- updated_at_ms
- expires_at_ms

Acceptance criteria:

- UI can refresh and recover a running/completed task by task id.
- Old completed/error tasks are pruned.
- Task state survives app restart.

### P1.2 Add bounded task queue

Replace direct `asyncio.create_task()` with a bounded queue/worker abstraction.

Acceptance criteria:

- Concurrent meeting jobs are capped.
- New jobs return 429 or queued status when saturated.
- Long jobs can be cancelled.
- UI shows queued/running/completed/failed states.

### P1.3 Add structured frontend state model

Introduce a small `appState` object in the UI JavaScript:

```js
const appState = {
  auth: { status: 'unknown', score: null, expiresAt: null },
  graph: { status: 'unknown' },
  llm: { status: 'unknown' },
  dispatch: { status: 'unknown' },
  recording: { status: 'idle' },
};
```

Acceptance criteria:

- Status pills render from state, not ad-hoc DOM edits.
- Failed refreshes are visible.
- Manual retry button calls the same state refresh function.

### P1.4 Add backend integration tests

Use FastAPI `TestClient` or `httpx` to cover:

- `/healthz`
- `/readyz`
- `/status`
- `/events`
- upload validation failures
- session status lifecycle
- meeting task status not found
- owner override disabled/no-token behavior

### P1.5 Add browser smoke tests

Use Playwright or a lightweight JS DOM approach after the UI is split out of inline `app.py`.

Flows:

- page loads with status pills
- trusted session persists across new captures
- manual verify bypasses cache
- forget trusted session clears auth state
- graph unavailable shows degraded state
- meeting task progress survives refresh

## P2 Reliability Work

### P2.1 Split UI assets from `app.py`

Move the inline template into:

```text
voice-agent/src/voice_agent/server/templates/capture.html
voice-agent/src/voice_agent/server/static/app.css
voice-agent/src/voice_agent/server/static/app.js
```

Acceptance criteria:

- `app.py` becomes route/service code only.
- UI JavaScript can be unit-tested independently.
- Browser caching gets explicit versioning.

### P2.2 Add observability

Add structured logs and counters:

- verify attempts / accepted / rejected / errors
- upload rejected by size/type
- meeting tasks queued/running/completed/error
- graph writes success/fail
- dispatch success/fail
- LLM test success/fail

Optional endpoint:

```http
GET /metrics
```

### P2.3 Add deployment profiles

Separate local-dev and exposed-deployment compose files:

- `docker-compose.yml`: local dev defaults
- `docker-compose.prod.yml`: no hardcoded secrets, stricter limits, restart policy, ready healthcheck
- `.env.example`: documented variables only

## Immediate Implementation Order

1. Apply and test the UI session refresh patch.
2. Remove the committed owner override secret from compose.
3. Add `.env.example`.
4. Add upload size/type guardrails.
5. Add server-side trusted session endpoints.
6. Expand `/readyz`.
7. Convert meeting task manager to persisted SQLite state.
8. Split UI into static assets/templates.

## Reliability Gate Before Calling This Production-Ready

Sophia should not be treated as reliable until all of the following pass:

```bash
cd voice-agent
pytest
python scripts/patch_ui_session_refresh.py
pytest tests/test_ui_session_refresh_patch.py
docker compose build
docker compose up -d
curl -fsS http://127.0.0.1:8765/healthz
curl -fsS http://127.0.0.1:8765/readyz
curl -fsS http://127.0.0.1:8765/status
```

Manual browser checks:

- Authenticate once.
- Record three captures without repeated auto-auth prompts.
- Refresh browser page and confirm auth status is still trusted until expiry.
- Click manual Verify and confirm it performs a fresh check.
- Click Forget trusted session and confirm auth-only actions disable.
- Stop Neo4j and confirm capture still saves locally with clear degraded state.
- Restart the container and confirm `/readyz` and the UI recover cleanly.

## Decision

The UI patch is the correct first move, but reliability requires server-side session state, stricter upload handling, safer secret management, better readiness checks, and persisted task state. Those are the next implementation cycle priorities.
