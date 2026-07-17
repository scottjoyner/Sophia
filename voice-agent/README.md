# voice-agent

A lightweight Sophia voice overlay service intended for Hermes-agent workflows.
It keeps voice orchestration (STT/VAD/auth/TTS) separate from the core agent runtime
and supports protocol adaptation for containerized communication.

## Design goals
- Overlay architecture: voice I/O is decoupled from core agent logic.
- Container-friendly protocol: supports native websocket and `hermes_overlay_v1` envelope.
- Low-memory defaults (5-10 GB target): tiny/int8 STT profile, fallback TTS, mock/openai-compatible LLM.

## Quickstart
```bash
pip install -e .
voice-agent serve --host 0.0.0.0 --port 8765 --config configs/dev.yaml
```

## Container deployment
```bash
cd ~/git/Sophia/voice-agent
docker compose up --build
curl http://127.0.0.1:8765/healthz
curl http://127.0.0.1:8765/status
```

The container config uses `hermes_overlay_v1`, writes artifacts under `/data`,
and keeps the low-memory STT profile (`tiny`, `int8`, two CPU threads).
It also defaults the memory graph to the local Neo4j container exposed on
`bolt://host.docker.internal:7687`, using the `neo4j` database. Browser
Browser captures are written inside the container at `/captures`; the compose file
binds that path to `/media/scott/NAS5/fileserver/audio/sophia-captures` on
this machine.

The capture page also sends context with each saved clip: a stable browser
device id, hashed device fingerprint, user agent, language, timezone, screen
and hardware hints, optional browser geolocation, client IP, and an optional
human activity note. Neo4j stores the useful fields directly on
`SophiaCapture` and links captures to a `Device` node when a device id exists.

When you are ready to train a voiceprint from the graph, provide credentials
at runtime:

```bash
export NEO4J_URI=bolt://host.docker.internal:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=...
export NEO4J_DATABASE=neo4j
export NEO4J_DEFAULT_SPEAKER=scott
docker compose up --build
```

Intent detection defaults to the local heuristic pass-through path. To use a
small draft model for intent classification, expose any OpenAI-compatible
endpoint and set:

```bash
export SOPHIA_INTENT_PROVIDER=hermes
export SOPHIA_INTENT_BASE_URL=http://host.docker.internal:8000
export SOPHIA_INTENT_MODEL=small-draft-model
```

## Hermes integration
Hermes now has a bundled `sophia_voice` plugin in `~/git/hermes-agent`.
Set the sidecar URL before launching Hermes or the dashboard:

```bash
export SOPHIA_VOICE_URL=http://127.0.0.1:8765
hermes dashboard --tui
```

The plugin exposes:
- `sophia_voice_status`
- `sophia_voice_intent`
- `sophia_voice_chat`
- `sophia_voice_events`

The dashboard discovers an optional Sophia Voice tab with sidecar status,
recent voice events, and a transcript box that routes through Sophia's
Hermes-aware voice prompt path.

## SSD staging and auto-ingest

The NAS remains the archive tier. Dashcam videos stay on NAS because that tree
is very large. Audio is staged onto the local S drive SSD so transcription,
speaker work, and graph ingest can run without repeatedly pulling from NAS.

Current local paths:

- NAS audio source: `/media/scott/NAS5/fileserver/audio`
- NAS archive root in the container: `/nas-fileserver`
- S drive SSD staging root: `/mnt/S/sophia-ingest`
- SSD staging root in the container: `/ssd-ingest`
- Staged audio: `/mnt/S/sophia-ingest/audio`
- Drop box for other systems: `/mnt/S/sophia-ingest/dropbox/inbox`

Stage NAS audio to SSD:

```bash
scripts/stage_nas_audio_to_ssd.py \
  --source /media/scott/NAS5/fileserver/audio \
  --dest /mnt/S/sophia-ingest/audio \
  --manifest /mnt/S/sophia-ingest/manifests/staged-audio.jsonl
```

Register staged files in Neo4j `memory` as pending ingest items:

```bash
docker exec voice-agent-sophia-voice-1 python /app/scripts/register_staged_audio.py \
  --manifest /ssd-ingest/manifests/staged-audio.jsonl \
  --uri bolt://host.docker.internal:7687 \
  --user neo4j \
  --password "$NEO4J_PASSWORD" \
  --database memory
```

For files dropped directly onto S from other machines, put them under
`/mnt/S/sophia-ingest/dropbox/inbox`, then run the same staging script with
that inbox as `--source` and a fresh manifest. For machines that can only reach
NAS, drop files into the NAS audio tree and let this machine stage them to S.

For a direct LLM backend, set `llm.provider: hermes` and `llm.base_url` in
the Sophia config to any OpenAI-compatible Hermes endpoint reachable from the
container. In dev, `llm.provider: mock` keeps the service runnable without
keys.

## Offline voice insight layer

Voice identity is handled as a clean graph overlay rather than rewriting the
legacy speaker nodes. Legacy diarization labels such as `SPEAKER_00` are treated
as clusters, not people. The overlay writes `VoiceIdentity`, `VoiceRecording`,
`VoiceSegment`, `VoiceUtterance`, `VoiceSpeakerCluster`, `VoiceTrainingSample`,
and `Voiceprint` nodes into Neo4j `memory`.

The local config lives in `configs/voice_insight.yaml`. It points at legacy
source data in Neo4j `neo4j`, writes promoted insight nodes to Neo4j `memory`,
maps old NAS paths into the container, and stores Scott training clips on the S
drive under `/mnt/S/sophia-ingest/voice-insight/training`.

Initialize and inspect the seed candidates:

```bash
docker exec voice-agent-sophia-voice-1 python /app/scripts/voice_insight.py \
  --config /app/configs/voice_insight.yaml init-schema

docker exec voice-agent-sophia-voice-1 python /app/scripts/voice_insight.py \
  --config /app/configs/voice_insight.yaml training-candidates --identity scott --limit 25
```

Export auditable training clips from configured seeds:

```bash
docker exec voice-agent-sophia-voice-1 python /app/scripts/voice_insight.py \
  --config /app/configs/voice_insight.yaml export-training-clips --identity scott --limit 100
```

Build a real speaker embedding voiceprint after installing the insight extra:

```bash
docker build --build-arg INSTALL_VOICE_INSIGHT_DEPS=1 -t voice-agent-sophia-voice:insight .

docker exec voice-agent-sophia-voice-1 python /app/scripts/voice_insight.py \
  --config /app/configs/voice_insight.yaml build-voiceprint --identity scott
```

Without the insight extra, `build-voiceprint` refuses to create a production
voiceprint. You can pass `--allow-fallback` only for smoke testing the graph
write path; that fallback vector is not suitable for speaker identity.

Build a balanced clone-training dataset from exported identity clips:

```bash
docker exec voice-agent-sophia-voice-1 python /app/scripts/voice_insight.py \
  --config /app/configs/voice_insight.yaml build-clone-dataset --identity scott
```

This emits both `manifest.jsonl` and `metadata.csv` under
`/ssd-ingest/voice-insight/training/<identity>/clone_dataset`, filtering out
too-short/too-long segments and limiting per-recording samples so mixed source
devices (dashcam, phone, recorder WAV) are represented.

For the next-stage auth + response policy objective (authenticate Scott when
detected, otherwise still respond in Scott clone voice), see
`docs/voice_auth_response_plan.md`.

For the multi-device Tommy relay/session-broker design, see
`docs/tommy-relay-architecture.md`. For the operator smoke-test, restart,
handoff, and browser/WebRTC verification checklist, see
`docs/tommy-relay-testing-guide.md`.

## Protocols
`server.protocol` in config controls websocket message parsing:
- `native_ws`: `{type, payload}`
- `hermes_overlay_v1`: `{protocol, frame: {type, payload}, meta}`

CLI replay and mic clients can use either protocol:

```bash
voice-agent replay --url ws://localhost:8765/ws --wav tests/fixtures/sample.wav --user scott --protocol hermes_overlay_v1
voice-agent mic --url ws://localhost:8765/ws --user scott --protocol hermes_overlay_v1
```

## HTTP endpoints
- `GET /healthz` - container liveness.
- `GET /readyz` - artifact directory and protocol readiness.
- `GET /status` - active sessions and runtime profile.
- `GET /events?after_id=0&session_id=...` - recent pipeline events.
- `WS /events` - live event stream.
- `POST /intent` - classify transcript and return the Hermes prompt.
- `POST /voice-chat` - classify transcript and run the configured LLM path.
- `POST /voiceprints/train-neo4j` - train a voiceprint from audio paths in Neo4j.
- `GET /memory-graph/status` - inspect the configured Neo4j memory graph target.

## Tommy relay endpoints
- `POST /relay/devices/register` - register or refresh a telescope/device endpoint. New devices receive a one-time `device_token`; set `SOPHIA_RELAY_ENROLLMENT_TOKEN` or `TOMMY_RELAY_ENROLLMENT_TOKEN` to require enrollment proof.
- `POST /relay/devices/heartbeat` - renew device liveness and active lease state.
- `GET /relay/devices` - list known relay devices without token hashes.
- `POST /relay/devices/{device_id}/trust` - admin-token-protected trust toggle.
- `POST /relay/devices/{device_id}/rotate-token` - admin-token-protected device token rotation; the new raw token is returned once.
- `POST /relay/devices/{device_id}/revoke` - admin-token-protected revocation; revoked devices cannot heartbeat, resume, negotiate WebRTC, or enter fallback promotion.
- `GET /relay/sessions` - list durable relay sessions.
- `POST /relay/sessions/{session_id}/attach` - attach only when the session is unowned/expired/parked, or when a valid resume token is supplied. Active sessions require handoff or force.
- `POST /relay/sessions/{session_id}/resume` - reconnect with `resume_token`, replay missed events/transcripts, and receive a fresh lease.
- `POST /relay/sessions/{session_id}/handoff` - atomically move the active lease to another device and emit revoke/takeover events.
- `POST /relay/sessions/{session_id}/force-handoff` - elevated/manual override path for emergency takeover.
- `POST /relay/sessions/{session_id}/detach` - release an active device lease and park the session.
- `POST /relay/sessions/{session_id}/expire` - manually expire an active lease and promote a fallback device when available.
- `GET /relay/sessions/{session_id}` - inspect active device, state, lease, fallback candidates, missing sequence ranges, and expected sequence.
- `GET /relay/sessions/{session_id}/timeline` - durable event/transcript timeline for replay/debugging.
- `GET /relay/sessions/{session_id}/gaps` - current sequence gap report.
- `POST /relay/sessions/{session_id}/audio` - HTTP fallback for sequenced audio chunks with duplicate rejection and gap detection.
- `POST /relay/sessions/{session_id}/transcript` - persist/emit transcript events and enqueue final text for async assistant processing.
- `POST /relay/sessions/{session_id}/webrtc/offer` - active-lease/device-token-protected durable SDP offer; returns ICE servers, answer/candidate/pending endpoints, and websocket fallback metadata. Negotiation status can be recovered through `/relay/sessions/{session_id}/webrtc/offers/{offer_id}/status`.
- `GET /relay/sessions/{session_id}/webrtc/offers/pending` - admin-token-protected list of unanswered offers for peer/SFU/helper discovery.
- `POST /relay/sessions/{session_id}/webrtc/offers/{offer_id}/answer` - active-lease/device-token-protected SDP answer recording.
- `POST /relay/sessions/{session_id}/webrtc/offers/{offer_id}/candidate` - active-lease/device-token-protected ICE candidate recording.
- `POST /relay/sessions/{session_id}/webrtc/offers/{offer_id}/status` - active-lease/device-token-protected durable negotiation recovery for the originating telescope.
- `WS /relay/sessions/{session_id}/stream` - bidirectional JSON websocket for `audio_chunk`, `transcript`, `heartbeat`, `resume`, event fetch frames, and live relay event push.
- `GET /relay/events?after_id=0&session_id=...` - durable relay event log.
- `GET /relay/live-events` - relay events from the in-process Sophia `EventBus`.
- `GET /relay/health` and `GET /relay/readiness` - relay DB/worker status.

CLI scaffold:

```bash
tommy-relay-agent --relay http://127.0.0.1:8765 --device-id x1-370 register --capability mic --capability speaker
tommy-relay-agent --relay http://127.0.0.1:8765 --device-id x1-370 --device-token dt_... attach
TOMMY_RELAY_ADMIN_TOKEN=... tommy-relay-agent --device-id x1-370 rotate-token
TOMMY_RELAY_ADMIN_TOKEN=... tommy-relay-agent --device-id x1-370 revoke --reason lost-device
```

## Recommended model profile (5-10 GB RAM)
- STT: `faster-whisper tiny` with `int8`, `cpu_threads=2`
- TTS: fallback (or Piper if prebuilt voice is available)
- LLM: external API/OpenAI-compatible endpoint, keep local memory pressure low

## CLI
```bash
voice-agent enroll --user scott --mic --phrases tests/fixtures/enroll_phrases.txt --n 5
voice-agent verify --user scott --mic --seconds 3
voice-agent mic --url ws://localhost:8765/ws --user scott
voice-agent replay --url ws://localhost:8765/ws --wav tests/fixtures/sample.wav --ref tests/fixtures/sample.txt --user scott
voice-agent bench --dataset dataset/manifest.jsonl --out runs/demo --config configs/bench.yaml
voice-agent report --run runs/demo/results.sqlite
voice-agent ingest-auto-ingest --neo4j-uri bolt://localhost:7687 --neo4j-user neo4j --neo4j-pass password --neo4j-database memory --user scott --speaker-name scott
```

Neo4j fingerprint enrollment uses the optional graph extra:

```bash
pip install -e ".[graph]"
```
