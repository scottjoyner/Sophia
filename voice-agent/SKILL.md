# Voice Insight Runbook Skill

This runbook defines the **operational workflow** for Sophia's offline voice insight pipeline:
- speaker cluster seeding
- training clip export
- voiceprint build
- clone dataset build
- promotion of legacy diarized segments into insight graph nodes

It is written so an operator can run the pipeline end-to-end reproducibly.

---

## 1) Scope and goals

Use this runbook when you need to:
1. Establish or update a voice identity (for example `scott`) from legacy transcriptions.
2. Build an auditable set of training clips for speaker verification/fingerprinting.
3. Build a curated clone-training dataset (`manifest.jsonl` + `metadata.csv`) from exported clips.
4. Promote diarized legacy segments into normalized `Voice*` graph nodes in Neo4j `memory`.

Non-goals:
- Real-time diarization in this flow.
- Creating production-quality voice clone model checkpoints directly in this script.

---

## 2) Prerequisites

### Infrastructure
- Running `voice-agent` container (`voice-agent-sophia-voice-1`).
- Neo4j reachable from container at `bolt://host.docker.internal:7687`.
- Databases:
  - source: `neo4j` (legacy graph)
  - target: `memory` (insight overlay)

### Environment variables
Set at runtime (or in compose env):

```bash
export NEO4J_URI=bolt://host.docker.internal:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD='<redacted>'
export NEO4J_SOURCE_DATABASE=neo4j
export NEO4J_DATABASE=memory
```

### Paths
Configured in `configs/voice_insight.yaml`:
- container training root: `/ssd-ingest/voice-insight/training`
- host training root: `/mnt/S/sophia-ingest/voice-insight/training`
- path rewrites map NAS/host paths into container-visible paths.

---

## 3) Graph model reference

The pipeline writes these node types in target DB:
- `VoiceIdentity`
- `VoiceSpeakerCluster`
- `VoiceRecording`
- `VoiceSegment`
- `VoiceUtterance`
- `VoiceTrainingSample`
- `Voiceprint`

Key relationships:
- `(:VoiceIdentity)-[:HAS_TRAINING_SAMPLE]->(:VoiceTrainingSample)`
- `(:VoiceIdentity)-[:HAS_VOICEPRINT]->(:Voiceprint)`
- `(:VoiceRecording)-[:HAS_VOICE_SEGMENT]->(:VoiceSegment)`
- `(:VoiceSegment)-[:HAS_UTTERANCE]->(:VoiceUtterance)`
- `(:VoiceSegment)-[:DIARIZED_AS]->(:VoiceSpeakerCluster)`
- `(:VoiceSpeakerCluster)-[:IDENTIFIES_AS]->(:VoiceIdentity)`

---

## 4) Command reference

All commands below run inside container context:

```bash
docker exec voice-agent-sophia-voice-1 python /app/scripts/voice_insight.py --config /app/configs/voice_insight.yaml <command>
```

Available commands:
- `init-schema`
- `legacy-speaker-report`
- `seed-legacy-speaker`
- `apply-configured-seeds`
- `promote-legacy-segments`
- `training-candidates`
- `export-training-clips`
- `build-voiceprint`
- `build-clone-dataset`

---

## 5) End-to-end workflow (recommended)

### Step 0 — initialize target schema

```bash
docker exec voice-agent-sophia-voice-1 python /app/scripts/voice_insight.py \
  --config /app/configs/voice_insight.yaml init-schema
```

Expected: `{"ok": true, "database": "memory"}`.

### Step 1 — inspect legacy speaker clusters

```bash
docker exec voice-agent-sophia-voice-1 python /app/scripts/voice_insight.py \
  --config /app/configs/voice_insight.yaml legacy-speaker-report --limit 50
```

Use this report to verify label quality and find clusters likely to belong to a person.

### Step 2 — seed identity mapping

Option A: configured seeds from YAML:

```bash
docker exec voice-agent-sophia-voice-1 python /app/scripts/voice_insight.py \
  --config /app/configs/voice_insight.yaml apply-configured-seeds
```

Option B: one-off manual seed:

```bash
docker exec voice-agent-sophia-voice-1 python /app/scripts/voice_insight.py \
  --config /app/configs/voice_insight.yaml seed-legacy-speaker \
  --identity scott --key 2000_1127_220512 --label SPEAKER_00 --confidence 0.90
```

### Step 3 — preview training candidates

```bash
docker exec voice-agent-sophia-voice-1 python /app/scripts/voice_insight.py \
  --config /app/configs/voice_insight.yaml training-candidates --identity scott --limit 25
```

Check transcript quality, durations, and source path rewrites.

### Step 4 — export auditable training clips

```bash
docker exec voice-agent-sophia-voice-1 python /app/scripts/voice_insight.py \
  --config /app/configs/voice_insight.yaml export-training-clips --identity scott --limit 100
```

Outputs:
- wav clips in `/ssd-ingest/voice-insight/training/scott/`
- `manifest.jsonl`
- `VoiceTrainingSample` nodes in Neo4j target DB

### Step 5 — build speaker voiceprint

Install insight deps in the image first:

```bash
docker build --build-arg INSTALL_VOICE_INSIGHT_DEPS=1 -t voice-agent-sophia-voice:insight .
```

Then:

```bash
docker exec voice-agent-sophia-voice-1 python /app/scripts/voice_insight.py \
  --config /app/configs/voice_insight.yaml build-voiceprint --identity scott
```

If speaker embedding model is unavailable, command intentionally fails unless `--allow-fallback` is provided.

### Step 6 — build clone-training dataset

```bash
docker exec voice-agent-sophia-voice-1 python /app/scripts/voice_insight.py \
  --config /app/configs/voice_insight.yaml build-clone-dataset --identity scott
```

Outputs (under `/ssd-ingest/voice-insight/training/scott/clone_dataset`):
- `manifest.jsonl`
- `metadata.csv` using pipe-separated fields: `audio_path|text|recording_id|duration_seconds`

Selection behavior is controlled by `training.clone_dataset` in YAML.

### Step 7 — promote legacy segments into overlay graph

Seeded-only promotion (safer first pass):

```bash
docker exec voice-agent-sophia-voice-1 python /app/scripts/voice_insight.py \
  --config /app/configs/voice_insight.yaml promote-legacy-segments --limit 1000 --batch-size 500
```

All clusters (broad pass):

```bash
docker exec voice-agent-sophia-voice-1 python /app/scripts/voice_insight.py \
  --config /app/configs/voice_insight.yaml promote-legacy-segments --all --limit 50000 --batch-size 1000
```

---

## 6) Clone dataset tuning guidance

Tune `training.clone_dataset` values in `configs/voice_insight.yaml`:
- `min_segment_seconds`: increase to avoid clipped/abrupt utterances.
- `max_segment_seconds`: lower if long monologues reduce phonetic diversity.
- `min_text_chars`: raise to filter non-linguistic snippets.
- `max_samples`: controls total dataset size.
- `max_samples_per_recording`: enforces cross-recording balance.

Practical baseline:
- 3–14s segments
- >=20 transcript chars
- 200–800 total clips depending on model/training budget

---

## 7) Quality gates before clone training

Before launching clone model training, verify:
1. Randomly audit >=25 clips for identity correctness.
2. Exclude noisy/multi-speaker leakage.
3. Ensure transcript text matches spoken words reasonably.
4. Confirm source diversity (not dominated by one device/session).
5. Rebuild dataset after any seed or threshold changes.

---

## 8) Troubleshooting

### `ModuleNotFoundError: No module named 'voice_agent'`
Use `PYTHONPATH=/app/src` in container context, or run via installed package entrypoint.

### `Neo4j password required: set NEO4J_PASSWORD`
Set `NEO4J_PASSWORD` for command runtime.

### `missing source_media` or clip export errors
Verify `path_rewrites` and host/container mount visibility.

### `build-voiceprint` refusing to run
Install image with `INSTALL_VOICE_INSIGHT_DEPS=1` and restart container.

### Too few clone dataset samples
Relax clone thresholds or increase exported clip limit.

---

## 9) Operational cadence

Recommended recurring cadence:
- Daily/weekly: ingest + candidate review.
- Weekly: export new clips + rebuild voiceprint.
- Weekly/biweekly: regenerate clone dataset and run quality audit.
- Monthly: revisit seeded mappings and thresholds.

---

## 10) Change management notes

When updating this pipeline:
- Keep identity attribution auditable (`legacy_*` fields preserved).
- Prefer additive graph overlay changes over destructive rewrites.
- Record config changes in commit messages and PR summaries.
- Re-run this runbook and capture command outputs in PR testing notes.

---

## 11) Voice auth + response policy objective

Reference implementation plan: `docs/voice_auth_response_plan.md`.

Operational objective:
- keep improving a robust Scott authentication model (voiceprint quality + threshold calibration),
- if detected voice is Scott, treat session as authenticated,
- if detected voice is not Scott/unknown, still respond using Scott cloned voice.

This means authentication state is tracked independently from TTS voice selection policy.

Before runtime rollout, complete the cross-repo STT/TTS review checklist in
`docs/voice_auth_response_plan.md` to lock integration contracts.
