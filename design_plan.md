# Sophia Voice Overlay Refactor Plan

## Status: Phase 1-2 in progress (2026-05-22)

Scott approved the voice fingerprint plan. Awaiting his voice samples from phone side to supplement dashcam extraction.

---

## Current architecture review
- Existing `voice-agent` implementation already separates concerns into STT, auth, LLM loop, and TTS pipeline.
- Websocket server supports `native_ws` and `hermes_overlay_v1` envelope protocols.
- STT defaults: `tiny` model, `int8` compute, 2 CPU threads (optimized for 5-10 GB RAM containers).
- Speaker verification: SpeechBrain ECAPA-TDNN (`spkrec-ecapa-voxceleb`) — code present but SpeechBrain NOT installed in container.
- Auth threshold in container.yaml: 0.1 (too low — needs 0.75 for ECAPA-TDNN).
- Voice insight pipeline: `/app/scripts/voice_insight.py` handles training sample discovery and voiceprint building.

## Refactor applied
1. Added protocol adapter layer with `native_ws` and `hermes_overlay_v1` decoders.
2. Added runtime/model profile settings in config to support memory-constrained deployments.
3. Switched streaming STT model selection to config-driven values (default tiny/int8).
4. Updated docs and dev config to align with Hermes overlay operation.
5. Added voice insight layer: VoiceIdentity, VoiceTrainingSample, VoiceSpeakerCluster nodes in Neo4j.
6. Added SSD staging pipeline: NAS → /mnt/S/sophia-ingest/ for transcription and speaker work.
7. Added voiceprint enrollment CLI: `voice-agent enroll`, `voice-agent verify`, `voice-agent bench`.

## Deployment shape
- Preferred as a lightweight sidecar container:
  - Websocket ingress from Hermes runtime.
  - Emits transcript/auth/tts events through shared run artifacts (DB + JSONL) and websocket acks.
- Optional split into microservices later:
  - `voice-ingress` (websocket + VAD)
  - `voice-inference` (STT/TTS/LLM orchestration)
  - `voice-auth` (speaker verification)

## Suggested Hugging Face options under 5-10 GB RAM
- STT: `faster-whisper tiny` or `base` int8 if latency allows.
- TTS: Piper lightweight voice, or fallback synth if no model volume is mounted.
- Speaker Verification: `speechbrain/spkrec-ecapa-voxceleb` (~100MB) — **needs to be installed**.
- Avoid loading both heavyweight local LLM + larger STT in the same container.

## Voice Fingerprint & Clone Plan

See `/home/scott/git/Sophia/PLAN.md` for comprehensive plan.

### Phase 1: Install SpeechBrain in Container
- Install `speechbrain` + `torchaudio` in container
- Set `runtime.allow_hf_downloads: true` in container.yaml
- Verify model downloads `spkrec-ecapa-voxceleb` (~100MB)

### Phase 2: Extract Clean Voice Samples from Dashcam
- Pick small set of dashcam days (start with 2024-01-01 through 2024-01-07)
- Extract audio from MP4 files using ffmpeg
- Run speaker diarization (Pyannote or WebRTC VAD + clustering)
- Identify Scott's speaker clusters from transcripts
- Extract clean WAV segments for each identified speaker

### Phase 3: Enroll Voiceprint
- Use existing `enroll.py` to generate embedding from clean samples
- Save to SQLite voiceprint registry
- Update Neo4j VoiceIdentity node
- Set auth.threshold to 0.75 in container.yaml

### Phase 4: Verify and Tune
- Test verification against Sophia's existing captures
- Adjust threshold, verify acceptance/rejection rates

### Phase 5: Voice Cloning for TTS
- Start with OpenVoice (lightweight, no training needed, works with ~42s samples)
- Upgrade to RVC when more voice data available (1-10 min clean samples)
- Consider ElevenLabs if quality is paramount and privacy acceptable

## Next integration step
- Wire Hermes-agent to send `hermes_overlay_v1` envelope frames and consume ack/event stream.
- Install SpeechBrain in container.
- Extract clean voice samples from dashcam/bodycam.
- Enroll Scott's voiceprint.
