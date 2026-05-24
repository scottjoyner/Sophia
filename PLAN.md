# Sophia Voice — Plan

## Status: Meeting Mode + Dispatch Hub Live (2026-05-24)

---

## Current State

### Voice Authentication
- **Voiceprint**: Built from 2+ live phone captures (ECAPA-TDNN 192-dim)
- **2025 dashcam data**: Confirmed to be a **different speaker** (scores 0.28 vs live voice at 0.88+)
- **Reference**: 10s phone capture scoring 0.97 against voiceprint
- **Threshold**: 0.60 (adjusted down from 0.75 for phone mic)
- **Registry**: SQLite at `/data/runs/results.sqlite`

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

### Phase 5: Async & Progress
- [ ] Background task processing for long meetings (return task ID, poll for result)
- [ ] WebSocket progress events during meeting processing (VAD → embed → cluster → transcribe → summarize)
- [ ] Live diarization over WebSocket (real-time speaker tracking)

### Phase 6: Multi-Microphone Robustness ✅ / 🔜
- [x] `voiceprint_devices` SQLite table (per-device embeddings)
- [x] Enroll with device_id parameter (stores device-specific voiceprint)
- [x] Verify iterates all device entries, returns best match + device_id
- [x] UI: device label input, matched device in auth result, devices in speaker list
- [ ] Evaluate cross-mic verification accuracy (known gap)
- [ ] Optional: UI for listing/managing/deleting per-device voiceprints

### Phase 7: Real-World Testing
- [ ] End-to-end WebSocket session with live auth
- [ ] Meeting mode with actual multi-speaker audio
- [ ] TTS quality evaluation with live voice reference
- [ ] Test dispatch hub with real assistx instance

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
