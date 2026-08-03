# Sophia ↔ Auto Platform Integration Refinement

Date: 2026-08-03

## Authority boundaries

The integrated platform has three distinct responsibilities:

- **Sophia** is the voice, capture, speaker-authentication, meeting, and response edge.
- **AssistX / auto-assist** is the durable intent, task, policy, review, lease, execution, and recovery authority.
- **auto-router** is the strict-offline OpenAI-compatible inference gateway. Sophia may request inference through router aliases, but must not select fleet nodes or own model placement.

Sophia must not bypass AssistX to create durable executable work, and must not bypass auto-router to perform fleet-aware model routing.

## Current integration defects found

### 1. Voice identity was lost at the AssistX boundary

Sophia emitted `actor`, `schema_version`, `correlation_id`, and `links` as top-level fields. The current AssistX `/api/voice/events` request model ignores those extra fields. As a result, the actor/auth state was not available to govern task creation and the original correlation ID was replaced.

### 2. HMAC verification used different bytes on each side

Sophia signed its full local event JSON. AssistX parsed that body into a smaller request model and then verified the signature against the re-serialized parsed model. Because extra fields were removed, HMAC-only deployments could reject otherwise valid Sophia callbacks.

### 3. Unverified speakers could enter executable event paths

The current AssistX endpoint creates tasks inline for `task_created` and `meeting_transcript`, but that endpoint does not yet enforce the canonical voice authorization state. Sophia therefore needs an edge-side guard until AssistX owns the final authorization decision.

### 4. Duplicate endpoint and taxonomy drift remains in AssistX

AssistX currently exposes both `/api/voice/events` and `/api/sophia/events`. The latter uses an older speaker-state taxonomy and different authentication behavior. These should be consolidated behind one canonical versioned event contract.

## First refinement slice implemented

The branch `sophia-auto-platform-contract-20260803` adds:

1. A compatibility serializer that retains Sophia's rich local outbox envelope but emits exactly the fields accepted by the current AssistX `VoiceEventIn` model.
2. Identity, authorization, schema, link, and correlation fields preserved inside `metadata` until the canonical server envelope is adopted.
3. One serialization path for both real dispatches and the `/dispatch/status` health probe.
4. An explicit action authorization boundary:
   - `authenticated_scott` and `admin_voice_override` may retain executable event types.
   - `registered_user_unverified` and `unknown_speaker` become review-only events.
   - `rejected` becomes an auditable rejected event and cannot dispatch.
5. Unknown actors no longer default to `user_id=scott`.
6. Regression coverage for HMAC payload shape, correlation preservation, auth taxonomy, connectivity probes, and durable task-outbox behavior.

## Required AssistX follow-up

AssistX should become the final authority for the voice authorization decision. The target implementation should:

1. Replace the duplicate voice request models with one versioned canonical envelope based on `assistx.contracts.event_envelope.EventEnvelope`.
2. Accept the current compatibility payload during a deprecation window.
3. Verify HMAC against a documented canonical byte representation or the original raw request body, not a lossy re-serialization.
4. Import the shared modern auth taxonomy:
   - `authenticated_scott`
   - `unknown_speaker`
   - `registered_user_unverified`
   - `admin_voice_override`
   - `rejected`
5. Create executable tasks only for trusted authorization states and allowed policy actions.
6. Route unverified requests to a review/clarification object without creating a READY executable task.
7. Preserve the supplied UUID correlation ID across SignalEvent, Intent, Task, Dispatch, TraceEvent, and response records.
8. Return a stable authorization decision in the response so Sophia can explain whether a request was accepted, queued for review, or rejected.

## auto-router refinement path

After the AssistX contract is unified, Sophia should use auto-router aliases rather than direct model/node discovery:

- interactive voice response: `auto/fast`
- tool planning or complex task extraction: `auto/high-quality` or `auto/code`
- explicit local fallback: `auto/local`

Desired request metadata for router observability:

- `workload_class=voice_interactive|voice_task_extract|meeting_summary`
- `session_id`
- `correlation_id`
- latency budget
- privacy scope
- streaming requirement

Sophia should consume the selected route/model metadata returned by auto-router for diagnostics, while leaving node assignment and model residency outside the Sophia process.

## End-to-end acceptance criteria

A production-ready voice path must demonstrate all of the following:

1. An authenticated owner request creates exactly one Intent and one Task and preserves one correlation ID end to end.
2. An unknown or unverified speaker cannot create or dispatch executable work.
3. A rejected speaker event is retained for audit without retaining sensitive audio longer than policy allows.
4. HMAC-only and Basic-auth deployments both pass the same contract tests.
5. Duplicate retries remain idempotent across Sophia's SQLite outbox and AssistX's graph state.
6. Barge-in/cancellation reaches the active task or response stream without waiting behind batch work.
7. Interactive response routing uses auto-router and remains strict-offline.
8. Every voice turn exposes latency spans for capture, VAD, STT, auth, intent, AssistX admission, router selection, first token, TTS first audio, and completion.
9. AssistX unavailability leaves the request durably pending in Sophia and reconciliation can deliver it later without duplication.
10. The default branch and deployment documentation are aligned (`main` across the auto-platform, or an explicitly documented exception for Sophia).
