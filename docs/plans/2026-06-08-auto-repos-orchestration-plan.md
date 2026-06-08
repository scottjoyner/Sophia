# Auto-Repos Orchestration Plan

> For Hermes / subagents: this is the canonical cross-repo implementation plan. Do not start implementation until this plan is committed and pushed in every listed repo. When implementing, take one task section at a time, keep changes repo-local unless a task explicitly spans repos, and verify contracts with tests before moving on.

**Goal:** Turn Sophia voice clips and other operator signals into traceable, routable, assignable work across the auto-* ecosystem without letting any single repo become a second control plane.

**Architecture:** Sophia remains the voice/client surface. AssistX / auto-assist is the canonical control plane and event timeline. auto-router owns lane/model/service routing decisions. auto-assign owns worker assignment, claims, leases, heartbeats, and completion. auto-ingest enriches the knowledge graph and evidence layer that routing and assignment consume. Neo4j stores durable graph state; Redis/queues are transport only, not the source of truth.

**Repos covered:**
- Sophia: `/home/scott/git/Sophia`
- auto-assist / AssistX: `/home/scott/git/auto-assist`
- auto-router: `/home/scott/git/auto-router`
- auto-assign: `/home/scott/git/auto-assign`
- auto-ingest: `/home/scott/git/auto-ingest`

**Non-goals for this phase:**
- Do not add a second assignment engine inside Sophia.
- Do not hardcode model/node choice in Sophia.
- Do not make auto-router mutate canonical task state except through documented route-decision events.
- Do not make auto-ingest responsible for real-time task execution.
- Do not hide missing claim/lease data by inventing fake “responding agent” labels in the UI.

---

## 1. System responsibility boundaries

### 1.1 Sophia responsibilities

Sophia owns:
- Voice capture from browser/mobile/voice surface.
- Browser-visible auth state for a clip: accepted/rejected, score, threshold, match source, fallback state.
- Dispatch UX: user can send an accepted voice event to AssistX.
- Operator trace display: timeline pulled from AssistX, plus local immediate auth result.
- Minimal diagnostics: AssistX webhook reachable, dispatch accepted/failed.

Sophia must not own:
- Durable task status.
- Worker selection.
- Model selection.
- Retry scheduling.
- Execution claims or leases.

### 1.2 auto-assist / AssistX responsibilities

AssistX owns:
- Public/private API endpoints for signal ingestion.
- Canonical `Dispatch`, `Intent`, `Task`, and `TraceEvent` state in Neo4j.
- Validation of signed webhooks from Sophia and other signal sources.
- The canonical timeline query used by Sophia and dashboards.
- Registration/projection of known nodes, services, capabilities, and current state.
- Contract bridge between incoming events and downstream routing/assignment.

AssistX must not own:
- Detailed lane policy or model scoring beyond storing router output.
- Worker-local runtime behavior beyond accepting heartbeat/claim/completion events.

### 1.3 auto-router responsibilities

auto-router owns:
- Lane selection: local, free_api, paid_api, heavy_reasoning, ingest, blocked, etc.
- Model/provider selection within a lane.
- Context source selection and preflight validation.
- Route-decision emission back to AssistX.
- Service registry projection for available model endpoints.

auto-router must not own:
- Durable task assignment/lease semantics.
- Sophia UI state.
- Voiceprint auth.

### 1.4 auto-assign responsibilities

auto-assign owns:
- Worker eligibility, scoring, and assignment recommendations.
- Claims, leases, lease renewal, release, reassignment.
- Heartbeat and completion events.
- Worker-visible queues / polls.
- Assignment outcome publication back to AssistX.

AssistX stores assignment events, but auto-assign owns the assignment lifecycle rules.

### 1.5 auto-ingest responsibilities

auto-ingest owns:
- Batch and streaming ingestion of external evidence: dashcam, phone logs, transcripts, docs, local files, fleet inventories.
- Entity extraction and graph enrichment.
- Evidence/context nodes consumed by router/assign decisions.
- Data locality metadata: hot SSD/S drive, durable Z/NAS, canonical Neo4j representations.

---

## 2. Canonical event model

All repos should converge on a small shared event envelope. Repo-specific fields belong under `payload`.

```json
{
  "event_id": "uuid-or-content-hash",
  "event_type": "voice.auth.accepted",
  "source": "sophia_voice",
  "schema_version": "2026-06-08.v1",
  "client_ts": "unix-seconds-or-iso8601",
  "correlation_id": "stable-id-linking-auth-dispatch-route-assignment",
  "session_id": "optional-session-id",
  "actor": {
    "user_id": "scott",
    "device_id": "browser-or-phone-device-id",
    "auth_state": "accepted|rejected|not_required"
  },
  "payload": {},
  "links": {
    "dispatch_id": "optional",
    "intent_id": "optional",
    "task_id": "optional",
    "route_id": "optional",
    "assignment_id": "optional"
  }
}
```

### 2.1 Required event types

Voice/auth events:
- `voice.auth.requested`
- `voice.auth.accepted`
- `voice.auth.rejected`
- `voice.auth.error`

Dispatch/control events:
- `dispatch.requested`
- `dispatch.accepted`
- `dispatch.rejected`
- `dispatch.cancelled`

Routing events:
- `route.requested`
- `route.selected`
- `route.failed`
- `route.blocked`

Assignment events:
- `assignment.requested`
- `assignment.recommended`
- `assignment.claimed`
- `assignment.heartbeat`
- `assignment.released`
- `assignment.completed`
- `assignment.failed`
- `assignment.expired`

Ingest/context events:
- `ingest.context.available`
- `ingest.context.updated`
- `ingest.evidence.linked`
- `ingest.evidence.missing`

### 2.2 Correlation requirements

Every cross-repo task must carry:
- `correlation_id`: generated at the first meaningful user signal, usually Sophia auth/dispatch.
- `dispatch_id`: generated by AssistX after accepting the dispatch.
- `task_id`: generated by AssistX when work becomes actionable.
- `route_id`: generated by auto-router when a lane/model decision is made.
- `assignment_id`: generated by auto-assign when a worker is selected/claimed.

If a repo cannot produce its downstream ID yet, it must still preserve upstream IDs unchanged.

---

## 3. Canonical trace model

AssistX should expose a trace endpoint that Sophia and dashboards consume.

### 3.1 Proposed endpoint

`GET /api/traces/{correlation_id}`

Response:

```json
{
  "correlation_id": "...",
  "current_state": "claimed|running|completed|failed|blocked|pending_assignment",
  "summary": {
    "voice_auth": "accepted",
    "dispatch": "accepted",
    "route": "selected",
    "assignment": "claimed",
    "worker": "xwing",
    "lane": "local",
    "model": "qwen-or-current-model"
  },
  "events": [
    {
      "event_id": "...",
      "event_type": "voice.auth.accepted",
      "source": "sophia_voice",
      "ts": "...",
      "payload": {}
    }
  ]
}
```

### 3.2 Trace state derivation

AssistX derives `current_state` from the latest high-priority event:
- `assignment.completed` -> `completed`
- `assignment.failed` -> `failed`
- `route.blocked` -> `blocked`
- active non-expired claim -> `running` or `claimed`
- route selected but no claim -> `pending_assignment`
- dispatch accepted but no route -> `pending_route`
- auth rejected or dispatch rejected -> `rejected`

---

## 4. Data contracts by repo

### 4.1 Sophia -> AssistX webhook

Sophia should POST to AssistX voice/event webhook using HMAC signing.

Minimum payload:

```json
{
  "event_id": "uuid",
  "event_type": "voice.auth.accepted",
  "text": "Voice auth: user=scott score=96%",
  "source": "sophia_voice",
  "session_id": "mobile-or-browser-session",
  "client_ts": "...",
  "metadata": {
    "schema_version": "2026-06-08.v1",
    "correlation_id": "...",
    "score": 0.96,
    "threshold": 0.75,
    "accepted": true,
    "user_id": "scott",
    "device_id": "...",
    "voiceprint_version_id": "...",
    "voiceprint_group_key": "...",
    "match_source": "active_head|historical_candidate|sample_vector",
    "fallback_used": false
  },
  "auto_dispatch": true
}
```

Required headers:
- `Content-Type: application/json`
- `X-Voice-Signature: sha256=<hmac_sha256_body>`

### 4.2 AssistX -> auto-router

AssistX should request a route when a dispatch becomes actionable.

Route request:

```json
{
  "correlation_id": "...",
  "dispatch_id": "...",
  "task_id": "...",
  "intent": {
    "type": "voice_command|debug_request|implementation_task|ingest_task",
    "text": "...",
    "priority": "low|normal|high|urgent"
  },
  "context_requirements": {
    "needs_repo": true,
    "needs_voice_auth": true,
    "needs_external_web": false,
    "needs_local_files": true
  },
  "eligible_lanes": ["local", "free_api", "paid_api", "heavy_reasoning"],
  "blocked_lanes": []
}
```

Route decision:

```json
{
  "event_type": "route.selected",
  "correlation_id": "...",
  "route_id": "...",
  "task_id": "...",
  "lane": "local",
  "provider": "lmstudio|openrouter|anthropic|openai|custom",
  "model": "model-name",
  "target_service": "service-id-or-url",
  "target_node_id": "x1-370|xwing|deathstar|macbook-air",
  "rationale": "short human-readable reason",
  "confidence": 0.0
}
```

### 4.3 AssistX / auto-router -> auto-assign

Assignment request:

```json
{
  "correlation_id": "...",
  "task_id": "...",
  "route_id": "...",
  "lane": "local",
  "target_node_id": "optional-router-preference",
  "required_capabilities": ["terminal", "file", "repo:Sophia"],
  "priority": "normal",
  "lease_seconds": 900,
  "payload_ref": "assistx-task-id-or-neo4j-node-id"
}
```

Claim event:

```json
{
  "event_type": "assignment.claimed",
  "correlation_id": "...",
  "assignment_id": "...",
  "task_id": "...",
  "worker_id": "xwing:hermes:session-id",
  "node_id": "xwing",
  "lease_expires_at": "...",
  "capabilities": ["terminal", "file"]
}
```

Completion event:

```json
{
  "event_type": "assignment.completed",
  "correlation_id": "...",
  "assignment_id": "...",
  "task_id": "...",
  "worker_id": "...",
  "result": {
    "status": "success",
    "summary": "...",
    "artifacts": []
  }
}
```

### 4.4 auto-ingest -> AssistX / Neo4j

Context availability event:

```json
{
  "event_type": "ingest.context.available",
  "correlation_id": "optional",
  "source": "auto_ingest",
  "payload": {
    "context_id": "...",
    "entity_ids": [],
    "evidence_refs": [],
    "storage_tier": "hot_ssd|durable_nas|neo4j_only",
    "freshness_seconds": 120
  }
}
```

---

## 5. Node and capability registry

AssistX should be the canonical projection endpoint for known nodes. auto-router and auto-assign can cache this, but should not maintain divergent truth.

Minimum node record:

```json
{
  "node_id": "xwing",
  "display_name": "xwing",
  "hostname": "xwing",
  "status": "online|offline|degraded|unknown",
  "tailscale_ip": "100.x.y.z",
  "roles": ["hermes_agent", "model_endpoint"],
  "capabilities": [
    {"kind": "agent", "name": "terminal", "status": "available"},
    {"kind": "model", "name": "fast_llm", "status": "available"}
  ],
  "services": [
    {
      "service_id": "xwing.lmstudio",
      "service_type": "llm.chat",
      "url": "http://100.108.99.47:1235/v1",
      "health_url": "http://100.108.99.47:1235/v1/models",
      "status": "online"
    }
  ]
}
```

Current known planning roles:
- `x1-370`: heavy reasoning, orchestration, local voice prep, primary local LLM endpoint.
- `xwing`: fast iteration, fresh Ubuntu replacement node, quick local model endpoint.
- `deathstar-XPS-8920`: VRAM-fit inference, auto-ingest and Neo4j-adjacent workloads.
- `Scott’s MacBook Air`: quick iteration and Sophia response prep.

---

## 6. Implementation phases

### Phase 0: Planning freeze and safety baseline

Objective: land this plan in every repo before implementation.

Tasks:
1. Add this plan to each repo under `docs/plans/2026-06-08-auto-repos-orchestration-plan.md`.
2. Commit and push the plan-only docs commit in each repo.
3. Do not mix unrelated code changes, secrets, generated caches, or local `.env` files into the plan commit.
4. Confirm every repo has a clean or intentionally-dirty status after the docs commit.

Verification:
- `git status --short --branch` in each repo.
- `git log -1 --oneline` in each repo shows a docs commit for this plan.
- `git push` succeeds for each repo.

### Phase 1: AssistX canonical event and trace API

Owner repo: `auto-assist`

Objective: make AssistX the source of truth for cross-repo traces.

Tasks:
1. Add/confirm Pydantic models for the canonical event envelope.
2. Add `TraceEvent` persistence in Neo4j with indexes on `correlation_id`, `event_type`, `task_id`, and `assignment_id`.
3. Update `/api/voice/events` to normalize incoming Sophia metadata into canonical trace events.
4. Add `GET /api/traces/{correlation_id}`.
5. Add `GET /api/traces/by-dispatch/{dispatch_id}` if dispatch ID is easier for UI lookup.
6. Add tests for trace state derivation.
7. Add tests for signed webhook -> dispatch -> trace event creation.

Acceptance criteria:
- A signed Sophia event creates a durable trace with `voice.auth.*` and `dispatch.*` events.
- The trace endpoint returns ordered events and derived current state.
- No secret values appear in logs or returned trace payloads.

Suggested tests:
- `tests/test_trace_events.py`
- `tests/test_voice_webhook_trace_integration.py`

### Phase 2: Sophia trace viewer becomes read-only over AssistX

Owner repo: `Sophia`

Objective: Sophia displays the canonical trace instead of inferring worker state locally.

Tasks:
1. Preserve local immediate auth result display.
2. After dispatch, capture `correlation_id` / `dispatch_id` / `task_id` from AssistX response.
3. Add backend proxy endpoint if needed: `GET /dispatch/trace/{correlation_id}`.
4. Update the Execution Trace panel to render AssistX trace events.
5. Show honest pending states: `pending_route`, `pending_assignment`, `claimed`, `running`, `completed`, `failed`.
6. Remove hardcoded worker inference from the UI once the AssistX trace endpoint is available.
7. Keep the static machine roster as a separate “known machines” section, not as proof of who claimed work.

Acceptance criteria:
- Before dispatch: Sophia shows local voice auth only.
- After dispatch: Sophia polls or refreshes the AssistX trace endpoint.
- Worker name only appears after `assignment.claimed` exists.
- UI never says “responding agent” without a claim event.

Suggested tests:
- Existing dispatch bridge tests should be extended.
- Add frontend-renderable JSON fixture tests if the project has a JS test path; otherwise test backend proxy and keep UI logic minimal.

### Phase 3: auto-router route decision contract

Owner repo: `auto-router`

Objective: route actionable AssistX tasks to lanes/models/nodes and publish `route.*` events.

Tasks:
1. Define route request/response models matching this plan.
2. Add an endpoint or worker path that accepts AssistX route requests.
3. Add support for `correlation_id`, `dispatch_id`, and `task_id` passthrough.
4. Use current policy and provider registry to select lane/model/service.
5. Emit `route.selected`, `route.failed`, or `route.blocked` back to AssistX.
6. Add tests for lane selection with voice-originated tasks.
7. Add tests that route decisions preserve correlation IDs.

Acceptance criteria:
- A route request returns a route decision with lane, provider/model/service, target node when known, and rationale.
- Route decisions are written back to AssistX and appear in the trace endpoint.
- Blocked/no-provider cases produce explicit `route.blocked` or `route.failed`, not silent failure.

Suggested tests:
- `tests/test_assistx_route_contract.py`
- `tests/test_route_event_writeback.py`

### Phase 4: auto-assign claim/lease lifecycle

Owner repo: `auto-assign`

Objective: make worker assignment real and visible.

Tasks:
1. Define assignment request, recommendation, claim, heartbeat, release, completion, and failure models.
2. Add polling or subscription from AssistX for route-selected tasks requiring assignment.
3. Implement worker eligibility scoring using required capabilities and node registry.
4. Implement claim creation with lease expiration.
5. Implement heartbeat renewal.
6. Implement completion/failure write-back to AssistX.
7. Add reassignment path for expired leases.
8. Add tests for claim, heartbeat, expiry, and completion.

Acceptance criteria:
- A routed task becomes claimable.
- A worker claim appears as `assignment.claimed` in AssistX trace.
- Expired claims release or requeue safely.
- Completion/failure appears in the same trace.

Suggested tests:
- `tests/test_assignment_contract.py`
- `tests/test_claim_lease_lifecycle.py`
- `tests/test_assistx_writeback.py`

### Phase 5: auto-ingest context/evidence integration

Owner repo: `auto-ingest`

Objective: make graph/evidence context available to routing and assignment without coupling ingest to execution.

Tasks:
1. Define `ingest.context.*` event output model.
2. Ensure ingested documents, transcripts, phone logs, dashcam outputs, and file inventories expose stable context IDs.
3. Add lightweight context lookup/query endpoint or script output consumed by AssistX/auto-router.
4. Publish context freshness and storage-tier metadata.
5. Add tests for context event generation from a representative ingest artifact.
6. Document how S-drive hot cache and Z/NAS durable store map to Neo4j context nodes.

Acceptance criteria:
- auto-ingest can publish context availability to AssistX or Neo4j.
- auto-router can request or receive context refs without reading raw storage directly.
- Trace events can link to evidence/context IDs.

Suggested tests:
- `tests/test_context_event_contract.py`
- `tests/test_storage_tier_metadata.py`

### Phase 6: End-to-end voice dispatch trace

Owner repos: all five

Objective: prove the complete chain with a voice-originated task.

End-to-end scenario:
1. Sophia verifies a voice clip.
2. Sophia dispatches signed event to AssistX.
3. AssistX stores dispatch and creates trace.
4. auto-router selects a route/lane/model/node.
5. auto-assign claims a worker and starts/heartbeats lease.
6. Worker completes or reports failure.
7. AssistX trace shows the full timeline.
8. Sophia displays the full timeline without guessing.

Acceptance criteria:
- One correlation ID links every event.
- Every repo preserves correlation ID in logs/events/tests.
- Operator can answer: “which machine/model/agent responded to this voice clip and why?”
- Failure at any stage is visible as a trace state, not hidden in logs.

---

## 7. Subagent work packets

### Packet A: AssistX trace substrate

Repo: `auto-assist`

Goal: implement canonical trace storage/query and signed voice event normalization.

Inputs:
- This plan.
- Existing AssistX `/api/voice/events` implementation.
- Existing Neo4j client patterns.

Deliverables:
- Models for canonical events.
- Persistence helpers.
- Trace query endpoint.
- Tests for signed webhook and trace derivation.

Do not modify:
- auto-router route policy.
- Sophia frontend beyond any documented response-shape compatibility notes.

### Packet B: Sophia trace consumer

Repo: `Sophia`

Goal: make the UI consume AssistX traces and avoid local worker inference.

Inputs:
- This plan.
- AssistX trace API response contract.
- Existing dispatch bridge tests.

Deliverables:
- Trace fetch/proxy support.
- UI rendering of canonical event states.
- Tests for dispatch response parsing and trace fetch behavior.

Do not modify:
- AssistX persistence.
- auto-router policy.
- auto-assign leases.

### Packet C: auto-router route write-back

Repo: `auto-router`

Goal: consume route requests and emit route decisions into AssistX trace.

Inputs:
- This plan.
- Existing provider/lane/policy config.
- AssistX trace event write endpoint.

Deliverables:
- Route request/response models.
- Route decision endpoint/worker.
- Write-back client.
- Tests for selected/blocked/failed outcomes.

Do not modify:
- auto-assign claim lifecycle.
- Sophia UI.

### Packet D: auto-assign worker lifecycle

Repo: `auto-assign`

Goal: implement assignment lifecycle events and worker claims.

Inputs:
- This plan.
- AssistX node registry/context projection.
- auto-router route decisions.

Deliverables:
- Assignment models.
- Claim/lease/heartbeat/completion logic.
- AssistX write-back client.
- Expiry/release tests.

Do not modify:
- Routing policy.
- Ingest pipelines.

### Packet E: auto-ingest context publisher

Repo: `auto-ingest`

Goal: publish context/evidence availability in a form router/assign can consume.

Inputs:
- This plan.
- Existing ingest outputs and Neo4j schema.
- Storage-tier layout: hot SSD/S-drive and durable Z/NAS.

Deliverables:
- Context event model.
- Stable context IDs.
- Storage tier metadata.
- Tests with representative artifacts.

Do not modify:
- Worker assignment.
- Sophia dispatch.

### Packet F: End-to-end validation harness

Repos: start in `auto-assist`, with fixtures from all repos as needed.

Goal: simulate voice auth -> dispatch -> route -> assign -> completion using local mocked repo clients.

Deliverables:
- Contract fixtures for all event types.
- A smoke test or script that validates correlation ID preservation.
- Operator runbook for manual E2E validation.

---

## 8. Testing strategy

Each repo should have three layers of tests:

1. Model/contract tests
- Validate JSON payloads and required fields.
- Preserve unknown metadata fields when safe.
- Reject malformed critical identifiers.

2. Integration tests with mocked peer repo
- Sophia mocks AssistX.
- AssistX mocks auto-router and auto-assign.
- auto-router mocks AssistX write-back.
- auto-assign mocks AssistX trace/write endpoints.
- auto-ingest mocks AssistX/Neo4j write target where needed.

3. Manual smoke test
- Use real local services on known ports.
- Avoid secrets in logs.
- Print correlation ID and trace URL.

Minimum smoke commands to maintain in docs/runbooks:

```bash
# AssistX health / projection
curl -s http://127.0.0.1:8000/health | jq .
curl -s http://127.0.0.1:8000/api/router/context-projection | jq '.nodes | length'

# auto-router health
curl -s http://127.0.0.1:8088/health | jq .

# Sophia dispatch status
curl -s http://127.0.0.1:8765/dispatch/status | jq .
```

---

## 9. Security and safety rules

- HMAC secrets stay server-side; never write them into frontend localStorage or docs.
- `.env` files are not committed.
- Generated `__pycache__`, test caches, and local DB files are not committed.
- Voiceprint sample paths may be sensitive; trace responses should include IDs and summary fields, not raw local filesystem paths unless operator-authenticated.
- Outbound messaging actions still require explicit approval or Sophia voice auth according to user preference.
- Any cross-repo worker execution must carry the verified actor/auth state when the task originated from voice.

---

## 10. Open questions before implementation

1. Should AssistX expose one generic `POST /api/events` for all canonical events, or keep voice-specific and assignment-specific endpoints with a shared internal model?
2. Should auto-router actively poll AssistX for route requests, or should AssistX call auto-router synchronously during dispatch?
3. Should auto-assign own a persistent queue, or operate from AssistX task state plus leases?
4. What is the first production worker adapter: Hermes local CLI, Paperclip, direct subprocess, or a queue worker?
5. What operator auth protects trace endpoints that may reveal task/evidence metadata?
6. How much of the node registry is manually registered versus discovered from Tailscale/service probes?

Default recommendations:
- Keep AssistX as canonical event ingest with generic internal event model.
- Start with AssistX calling auto-router synchronously for simple route decisions, then move to async if latency becomes a problem.
- Let auto-assign operate from AssistX task state plus leases before adding another durable queue.
- Use Hermes local worker adapter first because it is easiest to verify locally.
- Protect detailed trace endpoints at least as strongly as current AssistX operator endpoints.
- Combine manual node registration with health probes; do not rely on manual data alone for live status.

---

## 11. Rollout order and checkpoints

Checkpoint 1: plan-only commit exists in all repos.

Checkpoint 2: AssistX trace endpoint returns a synthetic trace.

Checkpoint 3: Sophia can render a synthetic AssistX trace.

Checkpoint 4: A real Sophia dispatch creates an AssistX trace.

Checkpoint 5: auto-router writes a real `route.selected` event.

Checkpoint 6: auto-assign writes a real `assignment.claimed` event.

Checkpoint 7: auto-assign writes completion/failure and Sophia displays final status.

Checkpoint 8: auto-ingest context refs appear in route rationale and trace payload links.

Only proceed to the next checkpoint after tests and one manual smoke verification pass.
