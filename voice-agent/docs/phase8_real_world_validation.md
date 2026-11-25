# Phase 8 real-world validation runbook

PLAN.md Phase 8 is five checks that can only be *proven* with real assets: real
speech, real models, real credentials, a real AssistX instance. This runbook is
the runnable version of that requirement.

One entrypoint runs all five and prints a per-item verdict:

```bash
cd voice-agent
python scripts/phase8_validation.py --out-dir ../reports/phase8
```

| item | what it proves | dedicated harness |
|------|----------------|-------------------|
| `ws-auth` | held-out real voice over `/ws` → `auth_decision` accepted, and a different speaker rejected | inline in `phase8_validation.py` |
| `meeting` | `POST /meeting/process` on real multi-speaker audio → task `done` with ≥2 distinct speaker labels | inline |
| `tts` | real TTS backend scored against a live voice reference (WER / ECAPA identity / spectral distance) | `scripts/tts_quality_eval.py` |
| `dispatch` | signed voice event accepted by the real AssistX endpoint, cross-checked via `/dispatch/status` | `scripts/dispatch_live_probe.py` |
| `fallback` | held-out clips scored through the live Neo4j historical-candidate search after the active head rejects them | `scripts/eval_fallback_candidate_search.py` |

Exit codes: `0` no item failed (skips are reported, not failed), `1` at least one
item failed, `2` usage/manifest error. `--strict` also fails on skips.

## Rules the harnesses enforce

1. **No fabricated numbers.** A missing prerequisite → `status: skipped`, exit 3,
   `metrics: {}`. Nothing is filled in with a placeholder.
2. **`phase8_evidence` is the gate.** It is only true when a real backend
   produced the figure *and* the check actually ran. A synthetic/stub/degraded
   run always reports `false`, and PLAN.md stays unticked.
3. **Placeholders are labelled.** `FallbackTTS` emits a 440 Hz tone; MOS is
   `PLACEHOLDER - requires a human listening panel`.
4. **The dashcam corpus is a known different speaker.** It may only appear as an
   impostor. A manifest that labels it as the target speaker is rejected with
   exit 2 (see PLAN.md "Current State": 0.28 vs 0.88+ for live voice).
5. **Side effects are opt-in.** Enrolment (`ws-auth`) and actionable dispatch
   (`task_created`, which creates a real AssistX task) need explicit flags.

## Per-item prerequisites and commands

### 1. `ws-auth` — live websocket voice auth

Needs: running server, real ECAPA-TDNN (`speechbrain` + `torch`) in the server
env, real faster-whisper model, one enrolment clip of the target speaker, one
**held-out** clip of the same speaker, one clip of a different speaker.

```bash
python scripts/phase8_validation.py --item ws-auth \
  --base-url http://127.0.0.1:8765 \
  --passphrase "$SOPHIA_PASSPHRASE" \
  --enroll-wav   /data/enroll/scott_01.wav \
  --verify-wav   /data/holdout/scott_09.wav \
  --impostor-wav /data/holdout/visitor_03.wav \
  --allow-enroll-side-effects \
  --confirm-ecapa-backend
```

* `--enroll-wav` writes a voiceprint version into the server registry and Neo4j
  lineage. Without `--allow-enroll-side-effects` the item skips: point
  `--base-url` at a scratch instance instead.
* Do not reuse the enrolment clip as the verify clip — it scores ~1.0 by
  construction and proves nothing.
* `--confirm-ecapa-backend` is an assertion, not a probe: `/status` and
  `/readyz` do not expose the speaker-embedder backend. Only pass it if you
  verified it (`pip show speechbrain` in the server env, and the server log line
  `Loaded ECAPA-TDNN` instead of `Using fallback embeddings`).
* A transcript of exactly `hello world` means faster-whisper has **no** model
  loaded (`src/voice_agent/stt/faster_whisper_stream.py` returns that string
  deterministically). The item reports `ok` for the transport but `evidence:
  false`.

### 2. `meeting` — multi-speaker meeting audio

Needs: `ffmpeg` on the host/container, real diarization backend, real
multi-speaker recording, expected speaker count.

```bash
python scripts/phase8_validation.py --item meeting \
  --base-url http://127.0.0.1:8765 \
  --meeting-audio /data/meetings/standup_2025-11-03.wav \
  --expect-speakers 3 \
  --confirm-diarization-backend
```

The item fails if the task does not settle, if it settles with `error`, or if the
transcript has fewer than `--expect-speakers` distinct speaker labels.

> Closed: the meeting conversion path now honours the same
> `runtime.audio_conversion_enabled` gate that readiness checks. With the default
> `true`, a missing ffmpeg is a **blocking** readiness failure, and when the gate
> is disabled the route decodes in-process instead of shelling out. A green
> `/readyz` with `audio_conversion_enabled=true` therefore does imply this route
> can convert audio.

### 3. `tts` — TTS quality against a live voice reference

Needs: a real backend (`tts.backend: openvoice|coqui|piper`) with model assets,
a live voice reference clip, and a human listening panel for MOS.

```bash
python scripts/tts_quality_eval.py \
  --out-dir ../reports/phase8/tts \
  --config configs/container.yaml \
  --reference-wav /data/holdout/scott_09.wav \
  --phrases phrases.txt
```

Reports per phrase: duration, RMS/peak, synthesis ms, WER (true Levenshtein over
whisper transcript), cosine similarity to the reference clip's ECAPA embedding,
and log-spectral distance to the reference. `--allow-placeholder-backend` proves
the plumbing on `FallbackTTS` only, and then every metric is reported as
`SKIPPED: backend is the FallbackTTS tone placeholder`.

### 4. `dispatch` — real AssistX

Needs: `ASSISTX_VOICE_WEBHOOK_BASE_URL` for the real instance and a matching
`ASSISTX_VOICE_WEBHOOK_SECRET`.

```bash
export ASSISTX_VOICE_WEBHOOK_BASE_URL=http://<assistx-host>:8000
export ASSISTX_VOICE_WEBHOOK_SECRET=<shared-secret>
python scripts/dispatch_live_probe.py --out-dir ../reports/phase8/dispatch
# add --confirm-side-effects to also send an actionable task_created event
```

The probe signs a review-safe `conversation_ended` event exactly the way
`server/assistx_dispatch.py` does, requires HTTP 200, and cross-checks
`/dispatch/status` on the running Sophia server. `--confirm-side-effects`
creates a **real AssistX task**.

The container default `http://host.docker.internal:8000` only resolves inside
Docker; on the host it is a DNS failure, not a dispatch failure.

### 5. `fallback` — historical candidate search on held-out clips

Needs: a labelled held-out manifest, Neo4j credentials, ECAPA embeddings.

```bash
export NEO4J_URI=bolt://127.0.0.1:7687     # host.docker.internal does not resolve on the host
export NEO4J_PASSWORD=<password>
python scripts/ensure_neo4j_schema.py
python scripts/eval_fallback_candidate_search.py \
  --manifest datasets/fallback/manifest.jsonl \
  --embeddings-cache reports/fallback_embeddings.json \
  --out-dir ../reports/phase8/fallback
```

Manifest columns: `clip_id`, `path`, `speaker`, `device_id`, `split`
(`enroll` / `eval`), `sha256`.

The script enrols the target speaker as N successive versions per device (each
superseding the last, which is what creates candidate lineage), then scores the
held-out clips through `verify_audio_segment` and reports:

* `rescues` — target clips the active head rejected that candidate search accepted
* `harms` — impostor clips the fallback accepted
* `fallback_fire_rate` — how often the fallback ran at all

A run where the fallback never fires proves nothing, and the report says so.
`--allow-synthetic-embeddings` runs a mechanism probe (`status: degraded`,
`phase8_evidence: false`) that must never be quoted as accuracy.

> Do not iterate on wrong Neo4j passwords: the server rate-limits authentication
> and returns `Neo.ClientError.Security.AuthenticationRateLimit`, which blocks
> further attempts.

## Where the output goes

```
reports/phase8/
├── phase8_report.json          # orchestrator: per-item status/evidence/fix
├── dispatch/dispatch_live_probe.json
├── tts/tts_quality_report.json + wav/ + tts_quality_summary.txt
└── fallback/fallback_candidate_report.json + fallback_trials.csv
```

## Tests

`voice-agent/tests/test_phase8_validation.py` covers the guard semantics (dashcam
rejection, skip exit codes, `phase8_evidence` never being true for a stub run,
WER correctness, event-envelope parsing) — not the real-world results, which need
the real assets above.