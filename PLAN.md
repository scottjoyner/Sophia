# Sophia Voice — Plan

## Status: Real STT + Debug Log + WebSocket E2E + Neo4j Voiceprint Lineage (2026-05-27)

---

## Current State

### Voice Authentication
- **Voiceprint**: Built from 2+ live phone captures, stored as immutable Neo4j versions with per-clip sample nodes and a current active head (ECAPA-TDNN 192-dim)
- **2025 dashcam data**: Confirmed to be a **different speaker** (scores 0.28 vs live voice at 0.88+)
- **Reference**: 10s phone capture scoring 0.97 against voiceprint
- **Threshold**: 0.60 (adjusted down from 0.75 for phone mic)
- **Registry**: Neo4j primary, SQLite compatibility mirror at `/data/runs/results.sqlite`
- **Search strategy**: active head first, then historical candidate search across version/sample embeddings when the primary match is weak or rejected

### UI (Capture Page — `GET /`)
- **Agent Mode**: Record → Verify Voice → Enroll Voice → Save Capture
- Auth result card with large score percentage + accepted/rejected badge
- Enroll button disabled until verify succeeds
- Graph status pill, capture counter, mobile-responsive layout

### TTS Backend
- **OpenVoice v2** (primary) — working, ~4.7s per utterance on CPU
- **Coqui XTTS** (fallback) — patched for PyTorch 2.12 compat, working

### Endpoints
| Route | Method | Purpose |
|-------|--------|---------|
| `/` | GET | Capture page (full UI) |
| `/healthz` | GET | Liveness |
| `/readyz` | GET | Readiness |
| `/status` | GET | Full runtime status |
| `/memory-graph/status` | GET | Neo4j connection status |
| `/events` | GET/WS | Event stream |
| `/intent` | POST | Classify transcript |
| `/voice-chat` | POST | Classify + LLM response |
| `/capture` | POST | Save audio + transcript to disk + Neo4j |
| `/auth/verify` | POST | Test audio against voiceprint |
| `/voiceprints/enroll` | POST | Add audio sample to voiceprint |
| `/voiceprints/train-neo4j` | POST | Train from Neo4j audio paths |
| `/voiceprints/status` | GET | Enrolled speakers list |
| `/meeting/process` | POST | Diarize + transcribe + summarize meeting audio |
| `/meeting/history` | GET | List past meetings from Neo4j |
| `/meeting/history/{id}` | GET | Get single meeting with segments |
| `/dispatch/to-assistx` | POST | Send voice event to auto-assist |
| `/dispatch/status` | GET | assistx connectivity check |
| `/ws` | WS | Real-time audio streaming (STT → Auth → LLM → TTS) |

### Data
- **Training clips**: 208 high-similarity 2025 clips (now separate speaker) + 2+ live phone captures
- **Neo4j memory graph**: VoiceIdentity, Voiceprint, VoiceTrainingSample, VoiceCapture nodes
- **Captures**: Saved to `/captures/` (bind-mounted to NAS) + Neo4j

---

## Roadmap

### Phase 1: Live Voice Enrollment ✅
- [x] Build voiceprint from user's phone captures
- [x] `POST /auth/verify` endpoint
- [x] `POST /voiceprints/enroll` endpoint  
- [x] Verify/Enroll buttons in UI
- [x] Auth result card with score + accepted/rejected

### Phase 2: UI/UX Polish ✅
- [x] Split page into Voice Auth + Capture sections
- [x] Auth result card with large score display
- [x] Enroll button only after successful verify
- [x] CSS state classes for auth pill (pass/fail/enrolling/idle)
- [x] Button variants, mobile-responsive layout
- [x] Capture counter persisted in localStorage

### Phase 3: Meeting Mode ✅
**Goal**: Process long-form multi-speaker audio (meetings, conversations) — diarize, transcribe, identify speakers, summarize.

**Approach**: SpeechBrain ECAPA-TDNN + Silero-VAD + sklearn clustering + faster-whisper per-segment.

**Implementation**:
- [x] `auth/diarization.py` — diarization module (Silero-VAD, ECAPA-TDNN, AgglomerativeClustering)
- [x] `POST /meeting/process` — upload + diarize + transcribe + summarize + Neo4j save
- [x] Three-mode toggle (Agent / Meeting / Dispatch) in UI
- [x] Meeting results view (timeline, transcripts, summary, download)
- [x] Speaker identification against enrolled voiceprints
- [x] LLM summarization of meeting transcript
- [x] Neo4j Meeting+MeetingSegment graph storage
- [x] Meeting history browse (`GET /meeting/history`, `GET /meeting/history/{id}`)
- [x] Per-segment transcript download button

### Phase 4: Dispatch Mode ✅
**Goal**: Bridge voice events to auto-assist for automated follow-up actions.

**Implementation**:
- [x] Dispatch Hub UI tab with send-to-assistx button
- [x] `POST /dispatch/to-assistx` — HMAC-signed voice events to assistx server
- [x] `GET /dispatch/status` — assistx connectivity check
- [x] Auto-dispatch toggle (localStorage persistence)
- [x] Dispatch voice_auth events on verify, meeting_transcript events on summarize

### Phase 5: Async & Progress ✅
- [x] Background task processing for long meetings (return task ID, poll for result)
- [x] Progress events via EventBus + `GET /meeting/status/{task_id}` polling
- [x] UI polls every 1.5s, updates progress bar + step text
- [ ] WebSocket progress events during meeting processing (nice-to-have)
- [ ] Live diarization over WebSocket (real-time speaker tracking)

### Phase 6: Multi-Microphone Robustness ✅
- [x] `voiceprint_devices` SQLite table (per-device embeddings)
- [x] Enroll with device_id parameter (stores device-specific voiceprint)
- [x] Verify iterates all device entries, returns best match + device_id
- [x] UI: device label input, matched device in auth result, devices in speaker list
- [x] Delete device endpoint + UI
- [ ] Evaluate cross-mic verification accuracy (known gap)

### Phase 7: Neo4j Voiceprint Lineage + Candidate Search
- [x] Store immutable voiceprint versions in Neo4j
- [x] Store per-clip voice sample embeddings in Neo4j
- [x] Preserve version lineage with `DERIVED_FROM`
- [x] Surface graph write-readiness in `/status`
- [x] Add Neo4j vector search on `VoiceprintVersion.embedding`
- [x] Add Neo4j vector search on `VoiceprintSample.embedding`
- [x] Return top-k candidate voiceprint versions for verification fallback
- [x] Compare current head vs historical candidates and choose best explanation
- [x] Add UI output for candidate alternatives and fallback reason

### Phase 8: Real-World Testing
- [ ] End-to-end WebSocket session with live auth
- [ ] Meeting mode with actual multi-speaker audio
- [ ] TTS quality evaluation with live voice reference
- [ ] Test dispatch hub with real assistx instance
- [ ] Verify fallback candidate search against held-out clips

### Phase 9: UI Polish
- [x] Speaker timeline visualization (color-coded horizontal bar)
- [x] Speaker filter buttons (click to isolate one speaker)
- [x] Per-speaker color coding on segment borders
- [x] Delete device from speaker management UI
- [x] Delete meeting from history UI
- [x] Debug event log panel (collapsible, auto-refresh every 5s)

### Phase 10: STT Infrastructure
- [x] faster-whisper + torch + ctranslate2 installed in container
- [x] Docker `INSTALL_VOICE_INSIGHT_DEPS=1` enabled in docker-compose.yml
- [x] Real STT verified (training clip transcribed correctly)
- [x] WebSocket pipeline verified end-to-end

---

## Key Files

| File | Purpose |
|------|---------|
| `voice-agent/src/voice_agent/server/app.py` | FastAPI app, all routes, full UI (inline HTML+CSS+JS) |
| `voice-agent/src/voice_agent/auth/verify.py` | Speaker verification (cosine similarity) |
| `voice-agent/src/voice_agent/auth/enroll.py` | Voice enrollment from WAV files |
| `voice-agent/src/voice_agent/auth/registry.py` | SQLite voiceprint registry |
| `voice-agent/src/voice_agent/auth/speaker_embedder.py` | ECAPA-TDNN embedding |
| `voice-agent/src/voice_agent/auth/diarization.py` | Speaker diarization via embedding + clustering |
| `voice-agent/src/voice_agent/tts/openvoice_tts.py` | OpenVoice v2 TTS backend |
| `voice-agent/src/voice_agent/tts/coqui_tts.py` | Coqui XTTS fallback backend |
| `voice-agent/src/voice_agent/server/pipelines.py` | STT → Auth → LLM → TTS pipeline |
| `voice-agent/configs/container.yaml` | Runtime config |
| `voice-agent/configs/dev.yaml` | Dev config |
| `voice-agent/configs/voice_insight.yaml` | Voice insight pipeline config |
| `voice-agent/scripts/voice_insight.py` | Offline voice insight tools |
| `voice-agent/scripts/test_e2e.py` | End-to-end WebSocket test |

## Infrastructure

- **Container**: `voice-agent-sophia-voice-1` (CPU only, 2 threads)
- **Port**: 8765 (WebSocket + HTTP)
- **Captures**: `/captures/` → bind-mounted to NAS `audio/sophia-captures`
- **Artifacts**: `/data/runs/` (events, SQLite registry, TTS output)
- **Voice data**: `/data/voice-insight/` (fingerprints, training clips, reference)
- **SSD staging**: `/mnt/S/sophia-ingest/` (NAS audio staged for processing)
- **Neo4j**: `bolt://host.docker.internal:7687` (memory database)

---

## Phase 11: Console Overhaul (UI/UX + Auth + Realtime AssistX) ✅

**Goal**: An intuitive, robust console that gates access, streams realtime answers from the
AssistX auto-router (small model), and automatically turns requests into tasks ingested into AssistX.

### Authentication logic
- A signed session gate replaces the open page. `POST /auth/login` validates a passphrase
  (`SOPHIA_APP_PASSWORD`, falls back to `SOPHIA_OWNER_OVERRIDE_TOKEN`) and issues an HMAC-signed,
  httpOnly, `SameSite=Strict` session cookie (`sophia_session`, 12h TTL).
- `GET /auth/session` reports auth state; `POST /auth/logout` clears the cookie.
- `require_session` (in `auth_session.py`) protects `/api/chat/stream`; unauthenticated calls get 401.
- The console shell checks `/auth/session` on load and shows a login card when unauthenticated.
- Voice identity remains available as an in-console "voice check" (mic → `POST /auth/verify`)
  surfacing the match score — layered on top of the session gate, not instead of it.

### Realtime responses from the AssistX auto-router
- `Assistant` (`server/assistant.py`) selects the LLM provider: the configured intent/auto-router
  endpoint (`SOPHIA_INTENT_BASE_URL` + `SOPHIA_INTENT_MODEL`, a small model is sufficient) when set,
  else the main provider, else a `MockProvider`.
- `OpenAICompatProvider.stream_complete` does OpenAI-compatible SSE (`stream:true`) token streaming.
- `POST /api/chat/stream` returns `text/event-stream` SSE. Events:
  `token` (realtime text delta), `tasks` (extracted task list), `ingested` (AssistX dispatch results),
  `error`, `done`. The UI renders tokens live with a typing caret.

### Automatic task assignment + AssistX ingestion
- After each reply, the small model extracts a JSON task list from the conversation
  (`Assistant.extract_tasks`) — title, description, priority, due, assignee.
- Each task is wrapped as a `task_created` event via `build_voice_event` and posted to AssistX
  (`dispatch_to_assistx`, HMAC/Basic-auth signed) — `Assistant.ingest_tasks`.
- The console "Task Inbox → AssistX" panel shows each task's lifecycle: `extracted → ingested ✓`
  (with AssistX `task_id`) or `failed` (with reason). No manual dispatch button required.

### Robustness
- Graceful degradation: with no LLM configured the assistant streams a mock; with no AssistX secret
  configured, ingestion reports the honest `failed` state rather than silently dropping tasks.
- Error toasts, disabled-send-while-streaming, Enter-to-send, auto-scroll, mobile-responsive layout,
  and a collapsible task inbox on small screens.
- All AssistX signing/dispatch logic is centralized in `server/assistx_dispatch.py` and reused by both
  the legacy `/dispatch/to-assistx` route and the new assistant.

### New endpoints & files
| Route | Purpose |
|-------|---------|
| `GET /` | New console SPA (login gate + streaming chat + task inbox) |
| `GET /legacy` | Previous capture/meeting/dispatch UI |
| `GET /auth/session` | Auth state |
| `POST /auth/login` | Passphrase → session cookie |
| `POST /auth/logout` | Clear session |
| `POST /api/chat/stream` | SSE realtime assistant + auto task ingestion |

| File | Purpose |
|------|---------|
| `server/console_ui.py` | `CONSOLE_PAGE` SPA template |
| `server/auth_session.py` | Signed session token + `require_session` |
| `server/assistx_dispatch.py` | Shared AssistX event builder + signed HTTP dispatch |
| `server/assistant.py` | Realtime reply, task extraction, AssistX ingestion |
| `llm/openai_compat_provider.py` | Added `stream_complete` (SSE) |
| `auth/voiceprint_graph.py` | `link_identity_to_global_speakers` + `get_identity_linkage` (global Speaker linking) |

### Voice authentication: override-as-training + global speaker linking
- Voice auth (`POST /auth/verify`) runs first. Below threshold → rejected, but the **Override & retrain**
  action stays available in the console identity strip.
- Override (owner key) calls `POST /voiceprints/owner-override-enroll`, which **auto-includes the clip**
  (`append=True`) and **retrains the speaker embedding** (mean of all enrolled samples); the console then
  re-verifies to show the improved score — the continuous-improvement loop for the owner voiceprint.
- Every identity-scope enrollment links the trained embedding into the **global Neo4j `Speaker` pool**
  (`VoiceprintGraphStore.link_identity_to_global_speakers`): it seeds/updates the owner's `Speaker` node with
  the embedding and `(VoiceIdentity)-[:IS_SPEAKER]->(Speaker)`. If another global `Speaker` already carries a
  matching embedding (>= `global_speaker_link_threshold`, default 0.85), it cross-links instead — the
  foundation for linking the owner to the rest of the global speakers.
- `GET /voiceprints/linkage` reports linked global speakers; `POST /voiceprints/link-speakers` forces a re-link.
- Config/env: `auth.global_speaker_link_enabled` (true), `auth.global_speaker_link_threshold` (0.85),
  `SOPHIA_GLOBAL_SPEAKER_LINK_ENABLED` / `SOPHIA_GLOBAL_SPEAKER_LINK_THRESHOLD`.

## Phase 12: Console Coherence, Hardening & Parity (2026-07-14)

**Goal**: Bring the new console to production quality — secure defaults, honest status, abuse
protection, feature parity with the legacy UI, and offline resilience.

### Secure-default warnings
- `create_app` logs a clear warning when `SOPHIA_SESSION_SECRET` (insecure default session secret) or
  `SOPHIA_APP_PASSWORD` (default `'sophia'`) is unset, so the service is never silently deployed insecure.

### Honest model status
- `Assistant.configured` (true when a real `OpenAICompatProvider` is selected) and `Assistant.model_label`
  are exposed under `/status` → `llm.assistant_configured` / `llm.assistant_model`, so the console pill
  shows `model: mock (no LLM configured)` instead of pretending an auto-router is connected.

### Abuse protection (rate limiting)
- `server/rate_limits.py` `InMemoryRateLimiter` is installed in `create_app`. Rules: `/auth/login`
  (10 req / 60s) and `/api/chat/stream` (30 req / 60s). The 11th login within a minute returns 429.

### Feature parity with the legacy UI
- The console is now a tabbed SPA covering every legacy capability:
  - **Chat**: streaming AssistX auto-router + auto task ingestion.
  - **Voice**: live voice check (`/auth/verify`), owner override & retrain
    (`/voiceprints/owner-override-enroll`), and a **Voiceprint Health** + **Global Speaker Linkage**
    panel polling `/voiceprints/status` and `/voiceprints/linkage`.
  - **Meetings**: upload → process → transcript/summary → dispatch (parity with legacy meeting mode).
  - **Dispatch**: manual event send + AssistX trace polling (`/dispatch/trace/<id>`) for full visibility.
- A live `WebSocket` (`/events`) drives the `liveDot` and event feed; AssistX traces poll automatically
  when a task is ingested, mirroring the legacy dispatch visibility.

### Offline-first resilience
- The console tracks connectivity via `navigator.onLine` and `online`/`offline` events (a `net:` pill in
  the top bar). Failed chat messages and voice checks are **queued** and automatically retried when the
  connection returns, so brief disconnects never lose an action.

### Tests
- `tests/test_console_overhaul.py`: login flow + cookie, 401 on unauth chat, SSE token/tasks/ingested
  shape, honest `/status` model reporting, graceful `/voiceprints/linkage` without Neo4j, and console +
  legacy homepage render. Full suite: 40 passed, 11 skipped (Neo4j-dependent).

## Phase 13: Longer-Term Voice Hardening — Adaptive Thresholds & Live Global-Speaker Linkage (2026-07-14)

**Goal**: Make voice auth robust across devices and turn the global-speaker linking (previously a
no-op without a configured graph) into a live, backfillable capability.

### Adaptive per-device threshold
- New `AuthConfig` fields: `adaptive_threshold_enabled` (true), `adaptive_threshold_min` (0.6),
  `adaptive_threshold_max` (1.0), `adaptive_threshold_alpha` (0.1, EMA rate), `adaptive_threshold_margin`
  (0.05). Env overrides: `SOPHIA_ADAPTIVE_THRESHOLD_*`.
- A `device_calibration` table in the voiceprint SQLite store records an EMA of accepted/rejected match
  scores per device (`util/db.py`), surfaced via `VoiceprintRegistry.record_device_outcome` /
  `fetch_device_calibration`.
- `verify.py` computes an **effective threshold per device**: `clamp(accepted_mean − margin, max(rejected_mean
  + margin, min), max)`. Genuine accepts on a good mic lower the bar (fewer false rejects); noisy devices
  tighten it. The chosen threshold and calibration are returned in the verify payload (`threshold_used`,
  `adaptive_threshold`, `device_calibration`) and shown in the console Voiceprint Health panel.
- Thresholds recorded after every verify so the system self-calibrates across devices over time.

### Live global-speaker linkage + backfill
- `VoiceprintGraphStore.link_identity_to_global_speakers` now **MERGEs** the `VoiceIdentity` (works for
  fresh identities, not just pre-enrolled ones) and writes the trained 192-dim embedding onto the global
  `Speaker` node, populating `speaker_embedding_idx`.
- New `link_global_speaker_by_label`: bridges the owner's `VoiceIdentity` to a global `GlobalSpeaker`
  whose `display_label` matches the user_id (e.g. `scott` → `GlobalSpeaker` "Scott") and writes the
  embedding there, so fleet diarization/linkage resolves to the same persona. Verified live: a `Speaker`
  node for `scott` was created with a 192-dim embedding and linked to `GlobalSpeaker` "Scott".
- New `backfill_global_speaker_embeddings`: re-runs linking for every enrolled identity so the vector
  index and global bridges stay current after a fresh DB / schema change / enabling linkage.
- New endpoint `POST /voiceprints/backfill-global-speakers` (owner-key protected) + console
  "Backfill global speakers" button, and `scripts/backfill_global_speaker_embeddings.py` CLI.

### Tests
- `tests/test_longer_term_voice.py`: adaptive-threshold math (disabled / no-calibration / lower-for-easy
  device / floor + rejected-score guard), device-calibration persistence in SQLite, and a Neo4j-gated
  live test for linking + backfill (skipped when `NEO4J_PASSWORD` is unset).
- Full suite: 45 passed, 12 skipped (Neo4j-dependent / offline).
