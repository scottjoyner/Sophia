# Sophia Voice Fingerprint & Clone Plan

## Status: Phase 1-2 in progress (2026-05-22)

Scott approved the plan. Awaiting his voice samples from phone side to supplement dashcam extraction.

---

## Current State (2026-05-22)

### Sophia Container (voice-agent-sophia-voice-1)
- **STT**: faster-whisper (tiny/int8, 2 CPU threads)
- **Speaker Verification**: SpeechBrain ECAPA-TDNN (spkrec-ecapa-voxceleb) — code present but SpeechBrain NOT installed in container
- **TTS**: Piper TTS / pyttsx3 fallback
- **Auth threshold**: 0.1 (too low — needs 0.75)
- **Capture dir**: /captures/ (bind-mounted to NAS audio/sophia-captures)
- **Artifacts**: /data/runs/results.sqlite (SQLite voiceprint registry)

### Existing Data in Neo4j (memory database)
| Node | Count | Notes |
|------|-------|-------|
| AudioFile | 36,931 | Indexed from /mnt/S/sophia-ingest/audio/ |
| SophiaCapture | 11 | Voice captures with transcripts |
| VoiceTrainingSample | 7 | All from one 2000/11/27 recording (~42 sec) |
| VoiceIdentity | 1 | scott |
| VoiceSpeakerCluster | 1 | legacy:2000_1127_220512:SPEAKER_00 |
| Speaker | 1 | scott |

### Existing Voice Samples (7 clips, all from 2000/11/27)
1. 10.4s: "Eating habit decisions..."
2. 7.7s: "eating habit decisions and the way that I facilitate..."
3. 6.4s: "And that's the way that I've solved that problem..."
4. 3.6s: "yeah I'm gonna buy all these healthy ingredients..."
5. 5.3s: "And like yeah there's an entire aisle of chips..."
6. 7.1s: "And like that shit isn't good for me..."
7. 2.0s: "But then my body feels like shit."

### NAS Audio Sources
| Source | Files | Size | Notes |
|--------|-------|------|-------|
| audio/2024/ | ~134K | 22GB | Year/month/day WAVs + transcriptions |
| audio/2025/ | ~116K | ~20GB | Same structure |
| audio/2026/ | ~27K | ~5GB | Same structure |
| audio/2000-2013/ | ~200 | ~2GB | Legacy recordings |
| R20260506-181757.MP3 | 1 | 137MB | Very recent large recording |
| dashcam/2024/ | 133K | **6.3TB** | MP4 video + metadata |
| dashcam/2025/ | 116K | **5.9TB** | MP4 video + metadata |
| dashcam/2026/ | 27K | **1.3TB** | MP4 video + metadata |
| bodycam MOVI0000.avi | 1 | 98MB | Has RTTM (4 speakers) |
| bodycam MOVI0002.avi | 1 | 484MB | Has RTTM data |

---

## Phase 1: Install SpeechBrain in Container

### Why
Without SpeechBrain, the SpeakerEmbedder falls back to mean/std/energy features — useless for speaker recognition.

### Steps
1. Install SpeechBrain + torchaudio in container
2. Set `runtime.allow_hf_downloads: true` in container.yaml
3. Verify model downloads `spkrec-ecapa-voxceleb` (~100MB)
4. Test embedder produces 192-dim ECAPA-TDNN embeddings

### Commands
```bash
docker exec voice-agent-sophia-voice-1 pip install speechbrain torchaudio
# Verify:
docker exec voice-agent-sophia-voice-1 python3 -c "from speechbrain.pretrained import EncoderClassifier; print('OK')"
```

---

## Phase 2: Extract Clean Voice Samples from Dashcam

### Strategy
Dashcam files are MP4 video. Need to:
1. Pick a small set of dashcam days (start with 2024-01-01 through 2024-01-07)
2. Extract audio from MP4 files using ffmpeg
3. Run speaker diarization to separate Scott from other speakers
4. Identify Scott's speaker clusters from transcripts
5. Extract clean WAV segments for each identified speaker

### Data Sources Priority
1. **audio/2024/ through audio/2026/** — Already has WAV files + transcriptions
2. **bodycam MOVI0000** — Has RTTM with 4 speaker labels (SPEAKER_00-03)
3. **dashcam MP4 files** — Need audio extraction first

### Existing RTTM Data (bodycam MOVI0000)
```
SPEAKER MOVI0000 1 10.823 2.428 <NA> <NA> SPEAKER_02
SPEAKER MOVI0000 1 14.287 0.560 <NA> <NA> SPEAKER_03
SPEAKER MOVI0000 1 15.407 2.292 <NA> <NA> SPEAKER_02
SPEAKER MOVI0000 1 17.699 0.390 <NA> <NA> SPEAKER_03
SPEAKER MOVI0000 1 19.261 0.407 <NA> <NA> SPEAKER_03
SPEAKER MOVI0000 1 24.440 3.922 <NA> <NA> SPEAKER_01
SPEAKER MOVI0000 1 31.995 1.019 <NA> <NA> SPEAKER_01
SPEAKER MOVI0000 1 32.980 1.341 <NA> <NA> SPEAKER_00
SPEAKER MOVI0000 1 34.491 1.053 <NA> <NA> SPEAKER_01
SPEAKER MOVI0000 1 35.000 0.730 <NA> <NA> SPEAKER_00
SPEAKER MOVI0000 1 36.630 0.374 <NA> <NA> SPEAKER_00
```

### Transcription (MOVI0000)
```
0,0.0,5.0,"I kind of just have a little bit of trouble too..."
1,10.54,13.34,"Yo, what's up? Say hello, Mom."
2,14.36,14.7,Hello.
3,15.44,17.66,"Oh, just wave. This doesn't have any audio."
4,18.32,18.32,
5,23.96,28.3,"This is my, uh, my alternative POV cam."
6,31.76,32.98,And it's rocking and rolling.
7,33.02,33.98,You can do a voiceover.
8,34.82,35.24,Absolutely.
```

### Path Resolution
- Container paths: `/mnt/8TB_2025/fileserver/...` → Host: `/media/scott/NAS1/fileserver/...`
- Auto-ingest resolved paths: `source_media_resolved` property in Neo4j

---

## Phase 3: Enroll Voiceprint

### Steps
1. Collect clean WAV segments from identified speakers
2. Use existing `enroll.py` to generate embedding
3. Save to SQLite voiceprint registry
4. Update Neo4j VoiceIdentity node
5. Set auth.threshold to 0.75 in container.yaml

### Commands
```bash
# Within container:
python3 -c "
from voice_agent.auth.enroll import enroll_from_files
from voice_agent.config import load_config
config = load_config('/app/configs/container.yaml')
enroll_from_files(config, 'scott', ['/path/to/audio1.wav', '/path/to/audio2.wav'])
"
```

---

## Phase 4: Verify and Tune

### Steps
1. Test verification against Sophia's existing captures
2. Adjust threshold from 0.1 to proper value (~0.75)
3. Verify acceptance/rejection rates are reasonable
4. Update container.yaml threshold

---

## Phase 5: Voice Cloning for TTS

### Technology Comparison

| Technology | Min Samples | Quality | Speed | GPU | Pros | Cons |
|------------|-------------|---------|-------|-----|------|------|
| **OpenVoice** | 3-10s ref | High | Real-time | No | Lightweight, no training, multi-lingual | Quality depends on reference, limited tone control |
| **RVC** | 1-10 min training | Very High | Medium | Yes | Best quality, voice conversion too | Needs GPU, training 10-60 min |
| **Coqui XTTS** | 5-30 sec ref | High | Medium | Yes | Multi-language, streaming | Heavy model (~600MB), slower |
| **ElevenLabs** | 1 min | Highest | Fastest | No | Best commercial quality | Paid, closed source, privacy |
| **Bark/Suno** | 10-30 sec | Medium | Slow | No | Free, expressive (laughs, pauses) | Lower quality |

### Recommendation
- **Start with OpenVoice** — lightweight, no training needed, works with existing ~42s samples
- **Upgrade to RVC** when more voice data available (1-10 min clean samples)
- **Consider ElevenLabs** if quality is paramount and privacy is acceptable

### Data Requirements
- Minimum viable (OpenVoice): 10-30s clean speech
- Good quality (RVC/XTTS): 1-3 min clean speech
- Best quality (RVC): 5-10 min clean speech from varied contexts

---

## Open Questions / Dependencies

1. **Scott's phone voice samples** — Awaiting user to add samples from phone side for verification
2. **Dashcam audio extraction** — Need ffmpeg in container or run extraction on host
3. **Speaker diarization** — Pyannote or WebRTC VAD + clustering for dashcam MP4 files
4. **SSD storage** — Staging large dashcam audio to /mnt/S/sophia-ingest/
5. **Voice cloning decision** — User needs to choose between OpenVoice, RVC, Coqui, ElevenLabs

---

## Key Files Reference

### Sophia Repo
- `/home/scott/git/Sophia/voice-agent/` — Voice agent service
- `/home/scott/git/Sophia/voice-agent/src/voice_agent/auth/speaker_embedder.py` — ECAPA-TDNN embedder
- `/home/scott/git/Sophia/voice-agent/src/voice_agent/auth/enroll.py` — Enrollment
- `/home/scott/git/Sophia/voice-agent/src/voice_agent/auth/verify.py` — Verification
- `/home/scott/git/Sophia/voice-agent/src/voice_agent/auth/neo4j_ingest.py` — Neo4j persistence
- `/home/scott/git/Sophia/voice-agent/configs/container.yaml` — Runtime config
- `/home/scott/git/Sophia/voice-agent/scripts/voice_insight.py` — Voice insight pipeline

### Container
- `/app/` — Python package
- `/captures/` — Incoming audio
- `/data/runs/results.sqlite` — Voiceprint registry
- `/data/runs/captures/` — Internal captures

### NAS
- `/media/scott/NAS1/fileserver/audio/` — Audio archive
- `/media/scott/NAS1/fileserver/dashcam/` — Dashcam videos
- `/media/scott/NAS1/fileserver/bodycam/` — Bodycam videos
- `/media/scott/NAS1/fileserver/headcam/` — Headcam videos

### Local SSD
- `/mnt/S/sophia-ingest/` — Staging area
- `/mnt/S/sophia-ingest/voice-insight/training/scott/` — Training samples (7 files, 3.6MB total)
