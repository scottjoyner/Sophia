# Sophia

## Status: Voice Fingerprint & Clone Plan Approved (2026-05-22)

Scott approved the voice fingerprint plan. Awaiting his voice samples from phone side to supplement dashcam extraction.

---

## The Current Hermes-Facing Service

The voice-agent service lives in `voice-agent/`. Use that deployment guide for containerized voice sidecar endpoints, Hermes dashboard integration, intent routing, and voice fingerprint enrollment.

```bash
cd voice-agent
pip install -e .
voice-agent serve --host 0.0.0.0 --port 8765 --config configs/dev.yaml
```

## Architecture

```
Mobile/Web Client → Caddy (HTTPS) → Voice Agent (FastAPI, port 8765)
    ├─ STT: faster-whisper (tiny/int8, 2 CPU threads)
    ├─ VAD: WebRTC VAD
    ├─ Speaker Verification: SpeechBrain ECAPA-TDNN (spkrec-ecapa-voxceleb)
    ├─ TTS: Piper TTS / pyttsx3 fallback
    ├─ LLM Intent: OpenAI-compatible provider
    └─ Neo4j: Speaker nodes, voiceprints, captures
```

## Voice Fingerprint & Clone Plan

See `PLAN.md` for the comprehensive plan.

### Phases
1. **Install SpeechBrain** in container (enables real ECAPA-TDNN speaker embeddings)
2. **Extract clean voice samples** from dashcam/bodycam/audio archives
3. **Enroll Scott's voiceprint** from clean samples
4. **Verify and tune** threshold (0.1 -> 0.75)
5. **Voice cloning for TTS** -- OpenVoice -> RVC -> ElevenLabs

### Technology Comparison (Voice Cloning)
| Technology | Min Samples | Quality | Speed | GPU | Status |
|------------|-------------|---------|-------|-----|--------|
| OpenVoice | 3-10s ref | High | Real-time | No | Start here |
| RVC | 1-10 min | Very High | Medium | Yes | Upgrade path |
| Coqui XTTS | 5-30 sec ref | High | Medium | Yes | Alternative |
| ElevenLabs | 1 min | Highest | Fastest | No | Commercial |
| Bark/Suno | 10-30 sec | Medium | Slow | No | Free option |

## Data Inventory

### Existing in Neo4j (memory database)
| Node | Count | Notes |
|------|-------|-------|
| AudioFile | 36,931 | Indexed from /mnt/S/sophia-ingest/audio/ |
| SophiaCapture | 11 | Voice captures with transcripts |
| VoiceTrainingSample | 7 | All from one 2000/11/27 recording (~42 sec) |
| VoiceIdentity | 1 | scott |
| VoiceSpeakerCluster | 1 | legacy:2000_1127_220512:SPEAKER_00 |
| Speaker | 1 | scott |

### NAS Audio Sources
| Source | Files | Size | Notes |
|--------|-------|------|-------|
| audio/2024/ | ~134K | 22GB | Year/month/day WAVs + transcriptions |
| audio/2025/ | ~116K | ~20GB | Same structure |
| audio/2026/ | ~27K | ~5GB | Same structure |
| audio/2000-2013/ | ~200 | ~2GB | Legacy recordings |
| dashcam/2024/ | 133K | **6.3TB** | MP4 video + metadata |
| dashcam/2025/ | 116K | **5.9TB** | MP4 video + metadata |
| dashcam/2026/ | 27K | **1.3TB** | MP4 video + metadata |
| bodycam MOVI0000.avi | 1 | 98MB | Has RTTM (4 speakers) |
| bodycam MOVI0002.avi | 1 | 484MB | Has RTTM data |

## Setup

```bash
cd voice-agent
pip install -e .
```

### Install SpeechBrain (required for speaker verification)
```bash
docker exec voice-agent-sophia-voice-1 pip install speechbrain torchaudio
```

### Container deployment
```bash
cd voice-agent
docker compose up --build
curl http://127.0.0.1:8765/healthz
curl http://127.0.0.1:8765/status
```

### Voiceprint enrollment
```bash
docker exec voice-agent-sophia-voice-1 python /app/scripts/voice_insight.py \
  --config /app/configs/voice_insight.yaml init-schema

docker exec voice-agent-sophia-voice-1 python /app/scripts/voice_insight.py \
  --config /app/configs/voice_insight.yaml export-training-clips --identity scott --limit 100

docker exec voice-agent-sophia-voice-1 python /app/scripts/voice_insight.py \
  --config /app/configs/voice_insight.yaml build-voiceprint --identity scott
```

## Hermes Integration

Hermes has a bundled `sophia_voice` plugin. Set the sidecar URL before launching:

```bash
export SOPHIA_VOICE_URL=http://127.0.0.1:8765
hermes dashboard --tui
```

The plugin exposes:
- `sophia_voice_status` -- Sidecar status, model profile, protocol
- `sophia_voice_intent` -- Classify transcript, return Hermes prompt
- `sophia_voice_chat` -- Voice chat through Sophia's Hermes-aware path
- `sophia_voice_events` -- Recent voice sidecar events for debugging

## Legacy

The original V0.1/V0.2 dictation experiments (`sophia_V.01`, `sophia_V.02`) are no
longer present in this repository. Their historical code was archived under
`archive/` (e.g. `archive/Sophia_io.py`). The maintained service is the
`voice-agent/` package described above.
