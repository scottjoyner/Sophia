# Offline Swarm Compute Architecture — Sophia Integration Plan

_Last updated: 2026-05-26_

## Role of this repo

Sophia is the voice/auth edge of the offline swarm. It should provide low-latency capture, VAD, STT, speaker verification, response voice selection, and Hermes-facing voice interaction. It should not become the global task scheduler or memory authority.

The global control plane should live in `scottjoyner/auto-assist`. Sophia should publish normalized events and authenticated input envelopes to AssistX, then receive response/action summaries to speak or display.

## Current baseline

Sophia currently includes:

- `voice-agent/` FastAPI service.
- WebRTC VAD.
- faster-whisper STT.
- SpeechBrain ECAPA-TDNN speaker verification.
- Piper / pyttsx3 TTS fallback.
- OpenAI-compatible LLM intent path.
- Hermes dashboard integration through the bundled `sophia_voice` plugin.
- Neo4j voice identity, voiceprint, capture, and training sample workflows.
- Voice-insight runbook for legacy speaker seeding, training clip export, voiceprint building, clone dataset building, and legacy segment promotion.

## Target Sophia responsibilities

1. Capture audio from phone/web/desktop clients.
2. Run VAD so silence/noise does not trigger transcription.
3. Run STT and preserve segment timestamps/confidence.
4. Score speaker identity against Scott voiceprint.
5. Emit explicit `auth_state`:
   - `authenticated_scott`
   - `not_scott_known`
   - `unknown_unverified`
6. Apply response voice policy.
7. Send normalized quick input/event envelopes to AssistX.
8. Store local outbox entries when AssistX is offline and replay later.
9. Preserve auditable links from clip/segment -> auth decision -> intent -> response.

## Contract with AssistX

Sophia should send AssistX a normalized quick-input envelope:

```json
{
  "event_id": "uuid-or-deterministic-key",
  "event_type": "voice.quick_input.created",
  "source_repo": "Sophia",
  "source_service": "voice-agent",
  "node_id": "registered-swarm-node-id",
  "occurred_at": "ISO-8601",
  "idempotency_key": "stable replay key",
  "subject": {
    "kind": "utterance",
    "id": "capture-or-segment-id"
  },
  "payload": {
    "text": "recognized transcript text",
    "language": "en",
    "stt_confidence": 0.0,
    "speaker_identity": "scott",
    "speaker_confidence": 0.0,
    "auth_state": "authenticated_scott",
    "auth_reason": "threshold_passed",
    "client_context": {}
  },
  "artifact_refs": [],
  "privacy": {
    "pii": true,
    "retention_class": "private-local"
  }
}
```

AssistX should return a response envelope with:

- response text.
- task/action status.
- approval requirements.
- suggested TTS voice.
- trace identifiers for `UserIntent`, `AgentRun`, and generated artifacts.

## Contract with auto-ingest

Sophia may depend on auto-ingest for:

- bulk audio discovery.
- transcript/source media normalization.
- artifact hashing/path canonicalization.
- long-running clip extraction or batch STT jobs.

Sophia should not directly own broad filesystem crawling outside voice-specific workflows. For broad media processing, it should request work through AssistX or call a documented auto-ingest job interface.

## Next implementation phases

### Phase 1 — Event envelope client

- Add a small AssistX event client with retry/outbox.
- Emit voice capture, STT complete, auth decision, and quick-input events.
- Use deterministic idempotency keys so replay is safe.

### Phase 2 — Runtime auth fields

- Add `auth_state`, `speaker_identity`, `speaker_confidence`, and `auth_reason` to runtime responses/events.
- Persist `VoiceAuthDecision` or equivalent graph events.
- Add threshold calibration artifact output.

### Phase 3 — Response voice policy

- Keep the already planned behavior: respond using Scott clone voice for all auth states unless a later policy says otherwise.
- Keep authentication state independent from TTS voice selection.
- Add config-driven policy so this can be changed safely.

### Phase 4 — AssistX quick-input loop

- Send authenticated quick inputs to AssistX.
- Receive response/action summaries from AssistX.
- Speak response with selected TTS voice.
- Attach AssistX trace IDs back to Sophia events.

### Phase 5 — Voice-insight scheduled work

- Register voice-insight batch jobs as swarm capabilities:
  - `voice.training_candidates`
  - `voice.training_clip_export`
  - `voice.voiceprint_build`
  - `voice.clone_dataset_build`
  - `voice.auth_calibration`
- Let AssistX dispatch and track these jobs.

## Design-decision questions for Scott

1. Should Scott-authenticated voice sessions be allowed to execute low-risk actions immediately?
2. Which actions are always high-risk and require AssistX UI approval even after voice auth?
3. Should unknown speakers be allowed to ask questions, submit notes, or only receive a limited status response?
4. Confirm: should Sophia always use Scott clone voice for responses, including unknown speakers?
5. What minimum speaker confidence should count as `authenticated_scott` for the first live test?
6. Should voice auth decisions be per utterance, per session, or both?
7. How long should raw auth clips be retained?
8. Should clone training clips be marked `protected` by default?
9. Should Sophia write directly to the shared Neo4j database, or only publish events to AssistX after the next integration phase?
10. Should Sophia run as a systemd user service, Docker service, or both?

## Immediate next docs after answers

- `docs/swarm_contracts/sophia_event_contract.md`
- `docs/swarm_contracts/voice_auth_policy.md`
- `docs/swarm_contracts/tts_voice_policy.md`
- `docs/swarm_contracts/assistx_quick_input_contract.md`
