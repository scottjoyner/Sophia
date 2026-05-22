# Voice Auth + Response Voice Policy Plan

## Objective

Continue improving Sophia's voice identity pipeline so runtime behavior is:

1. If the detected speaker is **Scott** (above acceptance threshold), treat the session as authenticated and respond normally.
2. If the detected speaker is **not Scott** (or unknown), still respond, but synthesize the response in **Scott's cloned voice**.

This separates **speaker authentication** from **response voice selection** while preserving a consistent assistant persona.

---

## Target behavior contract

For each utterance/session:

- compute speaker identity confidence against Scott voiceprint.
- classify auth state:
  - `authenticated_scott`
  - `not_scott_known`
  - `unknown_unverified`
- choose TTS voice policy:
  - `authenticated_scott` -> `tts_voice=scott_clone` (or optional direct passthrough policy)
  - `not_scott_known` -> `tts_voice=scott_clone`
  - `unknown_unverified` -> `tts_voice=scott_clone`

Recommended default for now: always output with `scott_clone`, while preserving auth state in metadata/events.

---

## Implementation phases

### Phase 1: Strengthen Scott authentication model

1. Expand high-confidence Scott seeds in `voice_insight.yaml`.
2. Export larger, audited training clip sets from mixed devices.
3. Rebuild `Voiceprint` with real embedding backend (`speechbrain`/equivalent).
4. Add threshold calibration pass:
   - evaluate genuine Scott clips vs imposter clips
   - tune `accepted_threshold` and `candidate_threshold`
5. Store calibration summary in graph or artifact file for traceability.

### Phase 2: Runtime auth decisioning

1. During STT/segment processing, score each segment against Scott voiceprint.
2. Aggregate segment-level scores into session-level auth state.
3. Emit explicit events/fields:
   - `speaker_identity`
   - `speaker_confidence`
   - `auth_state`
   - `auth_reason`

### Phase 3: Response voice routing

1. Add policy config section (example):

```yaml
voice_response_policy:
  target_identity: scott
  require_auth_for_identity_claims: true
  tts_voice_when_authenticated: scott_clone
  tts_voice_when_not_authenticated: scott_clone
```

2. In response pipeline, select TTS voice from `auth_state`.
3. Ensure all non-Scott users still hear Scott-clone output per requirement.

### Phase 4: Safety and auditing

1. Persist auth outcomes to Neo4j (`VoiceAuthDecision` node/edge or event log).
2. Add replayable audit trail: input segment -> score -> threshold -> decision.
3. Add alerts for drift (e.g., sudden drop in Scott match rate).

---

## Data quality gates (must-pass)

- Minimum audited Scott training hours target (set per sprint).
- Device diversity: dashcam + phone + recorder WAV represented.
- No heavy multi-speaker contamination in enrolled clips.
- Transcript sanity checks for clone dataset text.
- Versioned manifests for each retraining run.

---

## Suggested immediate next steps

1. Run `training-candidates` and expand Scott seeds beyond one recording.
2. Export 200-400 reviewed clips and regenerate Scott voiceprint.
3. Build a small threshold-eval script for genuine vs imposter score histograms.
4. Add runtime `auth_state` field in voice events.
5. Add config-driven response voice policy and set both branches to `scott_clone`.

---

## Cross-repo STT/TTS review (required before runtime rollout)

Because the final behavior depends on both recognition and synthesis quality,
run a coordinated review with Scott's STT and TTS repositories before shipping:

1. **STT repo review**
   - confirm diarization/segmentation boundaries are exposed in a way this
     pipeline can consume for auth scoring.
   - verify timestamps, confidence fields, and speaker labels are stable across
     noisy sources (dashcam, phone, recorder WAV).
   - identify where to attach `auth_state` and per-segment speaker confidence.

2. **TTS repo review**
   - confirm cloned Scott voice model versioning and artifact loading path.
   - verify inference options for low-latency vs high-fidelity response modes.
   - validate text normalization compatibility with generated `metadata.csv`.

3. **Integration contract review**
   - define shared event schema (`speaker_identity`, `speaker_confidence`,
     `auth_state`, `tts_voice_selected`, `policy_version`).
   - define failure policy (e.g., STT uncertain -> still `scott_clone` voice
     plus `unknown_unverified` auth state).
   - publish versioned interface notes so model or repo upgrades are auditable.
