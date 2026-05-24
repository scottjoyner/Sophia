# Sophia Voice — Plan

## Status: Live Voice Enrolled, UI Redesigned (2026-05-24)

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

### Phase 3: Meeting Mode 🔜 (Next)
**Goal**: Process long-form multi-speaker audio (meetings, conversations) — diarize, transcribe, identify speakers, summarize.

**Approach** (no pyannote — use SpeechBrain + sklearn):
1. Sliding window over audio (1.5s / 0.5s overlap)
2. Compute ECAPA-TDNN embedding per window
3. Cluster embeddings with sklearn (AffinityPropagation or AgglomerativeClustering)
4. Map clusters to time segments — produce speaker timeline
5. Transcribe each speaker segment with faster-whisper
6. Match clusters against enrolled voiceprints for name labels
7. Run LLM over full transcript for:
   - Action items
   - Key decisions
   - Requirements extraction
   - Per-speaker summaries
8. Save conversation to Neo4j memory graph

**UI**:
- Mode toggle (Agent / Meeting) at top of page
- Meeting mode: file upload + Process button
- Results display: speaker timeline, per-speaker transcripts, action items
- Download structured output (JSON)

**Implementation plan**:
- [ ] `auth/diarization.py` — diarization module (embed + cluster + segment)
- [ ] `POST /meeting/process` — long-form audio endpoint
- [ ] Mode toggle in UI
- [ ] Meeting results view (timeline + transcripts)
- [ ] Speaker identification against enrolled voiceprints
- [ ] LLM summarization of meeting transcript
- [ ] Neo4j conversation graph storage

### Phase 4: Multi-Microphone Robustness
- [ ] Enroll voice from multiple devices/mics
- [ ] Evaluate cross-mic verification accuracy
- [ ] Optional: per-device voiceprint profiles

### Phase 5: Real-World Testing
- [ ] End-to-end WebSocket session with live auth
- [ ] Meeting mode with actual multi-speaker audio
- [ ] TTS quality evaluation with live voice reference

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
