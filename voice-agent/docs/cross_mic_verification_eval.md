# Cross-Microphone Speaker-Verification Accuracy Harness

Closes the PLAN.md Phase 6 gap *"Evaluate cross-mic verification accuracy"*.

The harness takes a **labelled** set of clips and measures how the deployed
verification path behaves per recording device, with and without the adaptive
per-device thresholds (`AuthConfig.adaptive_threshold_*`, the `device_calibration`
table in `util/db.py`, and the effective-threshold maths in `auth/verify.py`).

Everything is scored through the **service verification path**
(`VoiceprintRegistry` + `auth.verify.verify_audio_segment`). Cosine similarity and
the adaptive-threshold formula are never re-implemented here, so the numbers move
when `verify.py` moves.

## What it measures

| Metric | Where it lands in the report |
|---|---|
| Confusion matrix at the default threshold (`--report-threshold`, default `0.60`) | `confusion_at_report_threshold` (tp/fp/tn/fn, accuracy) |
| FAR / FRR / TPR / balanced accuracy at the **effective (adaptive)** threshold | `pooled.adaptive`, `per_device.<id>.adaptive` |
| Same, at fixed global thresholds | `*.fixed_thresholds.base_global` / `.report_default` |
| Per-device accept rate | `per_device.<id>.adaptive.accept_rate` |
| ROC / AUC per device and pooled | `pooled.auc`, `per_device.<id>.auc`, `roc_points` |
| EER | `pooled.eer` |
| Same-device vs **cross-device** (clip's device was never enrolled) | `same_vs_cross_device` |
| Best single global threshold vs adaptive, on **held-out** data | `threshold_sweep.verdict` |

### Protocol

1. **enroll** clips → per-`(speaker, device)` voiceprints via
   `VoiceprintRegistry.save_device`, using the same embedding mean and the same
   `_prune_outlier_embeddings` outlier rejection that `auth/enroll.py` uses.
2. **calib** clips → run `verify_audio_segment` once per clip *with* calibration
   recording enabled, which seeds `device_calibration` exactly like live traffic does.
3. **eval** clips → scored with a `FrozenCalibrationRegistry` (reads calibration,
   refuses to record new outcomes). The held-out clips therefore cannot nudge the
   threshold they are being judged by, and the global threshold is fitted on the
   calibration split only. Nothing is self-graded.

Splits come from the manifest `split` column when present; otherwise they are cut
deterministically (`--seed`, `--calib-ratio`, `--holdout-ratio`).

## Manifest format

JSONL (one object per line), JSON array, or CSV/TSV with a header row.

Required columns: a clip `path` and a `speaker` label (the speaker identity).
Recommended: `device_id`. Optional: `split`, `clip_id`, `sha256` (embeddings-cache
key when the audio is not on disk).

```jsonl
{"path": "/captures/phone/a1.wav", "speaker": "scott", "device_id": "phone", "split": "enroll"}
{"path": "/captures/phone/a2.wav", "speaker": "scott", "device_id": "phone", "split": "calib"}
{"path": "/captures/phone/a3.wav", "speaker": "scott", "device_id": "phone", "split": "eval"}
{"path": "/captures/car/b1.wav",   "speaker": "scott", "device_id": "car-mic", "split": "eval"}
{"path": "/captures/car/b2.wav",   "speaker": "passenger", "device_id": "car-mic", "split": "eval"}
```

Column aliases accepted: `path|audio_path|clip_path|file|filename|audio`,
`speaker|user_id|speaker_id|target|label_speaker`,
`device_id|device|microphone|mic|mic_id|channel`, `split|subset|partition|fold|role`,
`clip_id|id|clip|name`, `digest|sha256|cache_key|clip_key`.

### What the dataset needs

* The **target speaker** (the one being enrolled — default: the speaker with the most
  `enroll` clips, or `--target-speaker`): ≥3 `enroll` clips per device you want the
  system to know, and ≥2 `calib` clips per device so calibration is not a single sample.
* ≥2 `eval` clips per device for the target speaker, **including at least one device
  that was never enrolled** — that is the cross-mic number.
* **Impostor clips** (`speaker` ≠ target) on as many devices as possible. Without them
  FAR cannot be computed and the harness says so in `warnings`.
* Same speaker, same session/phrase style across devices is fine and is what the
  adaptive threshold is designed for; no challenge phrase is used
  (`require_challenge` is forced off in the eval config).

## Running it

```bash
cd voice-agent

# 0) no labelled corpus ships in the repo: this builds a synthetic fixture
#    (random vectors, NOT ECAPA-TDNN) to prove the harness runs end to end
python scripts/make_synthetic_cross_mic_manifest.py --out-dir ../reports/synthetic
python scripts/eval_cross_mic_accuracy.py \
    --manifest ../reports/synthetic/manifest.jsonl \
    --out-dir ../reports/synthetic_run \
    --embeddings-cache ../reports/synthetic/embeddings.json

# 1) full run on your own clips: needs speechbrain + torch (ECAPA-TDNN)
python scripts/eval_cross_mic_accuracy.py \
    --manifest ../datasets/cross_mic/manifest.jsonl \
    --out-dir ../reports/cross_mic_run1

# 2) compute embeddings once, then re-score cheaply (no model, no audio, no download)
python scripts/eval_cross_mic_accuracy.py \
    --manifest ../datasets/cross_mic/manifest.jsonl \
    --out-dir ../reports/cross_mic_run2 \
    --embeddings-cache ../reports/cross_mic_embeddings.json
python scripts/eval_cross_mic_accuracy.py \
    --manifest ../datasets/cross_mic/manifest.jsonl \
    --out-dir ../reports/cross_mic_sweep_055 \
    --embeddings-cache ../reports/cross_mic_embeddings.json --base-threshold 0.55

# threshold sweep against a *non-adaptive* build of the same corpus
python scripts/eval_cross_mic_accuracy.py ... --adaptive-threshold disabled

# machine-readable stdout instead of the summary
python scripts/eval_cross_mic_accuracy.py ... --json
```

Writes to `--out-dir`:

* `cross_mic_report.json` — full machine-readable report (`sophia.cross_mic_eval.v1`)
* `cross_mic_summary.txt` — the human summary printed to stdout
* `cross_mic_trials.csv` — one row per verification attempt (calibration + eval)

Exit codes: `0` report written, `1` unexpected failure, `2` manifest/usage error,
`3` **skipped** (no ECAPA-TDNN backend and no usable embeddings cache).

Useful flags: `--holdout-ratio` / `--calib-ratio` / `--seed` (split control),
`--base-threshold` (global threshold under test), `--report-threshold` (confusion
matrix threshold), `--tolerance` (balanced-accuracy tie band for the verdict),
`--target-speaker`, `--no-sweep`, `--keep-work`, `--config` / `--artifacts-dir`.

## Graceful degradation (no fake numbers)

* ECAPA-TDNN needs `speechbrain` + `torch`. When `SpeakerEmbedder` cannot load the
  model it silently falls back to 3-value statistic vectors that carry no speaker
  identity. The harness detects this, and by default **refuses to run**: it writes a
  report with `"status": "skipped"` plus the exact reason (missing packages and the
  embedder's own warning) and exits `3`. No accuracy numbers are emitted.
* `--allow-fallback-embeddings` runs anyway, but the report is marked
  `"status": "degraded"` with a warning that FAR/FRR/AUC are not measurements.
* Embeddings produced from a **synthetic** cache are tagged
  `backend.cache_metadata.source = "synthetic"` and every report carries a warning.
* The harness runs against a scratch `artifacts_dir` with the Neo4j password dropped,
  so it never touches the deployed registry/graph and never depends on a live graph.

## Embeddings cache

`--embeddings-cache PATH` is content-addressed (`sha256:<digest of the clip>`), so the
cache survives files being moved and can be shared between machines. On a cache hit no
audio is read and no model is loaded, which is what makes threshold sweeps cheap.
Entries missing from the cache are embedded once and written back (the file is saved at
the end of a successful run; use `--write-embeddings-cache` to write somewhere else).

## Interpreting `threshold_sweep`

* `fit_split.fitted_global_threshold` — the single global threshold maximising balanced
  accuracy **on the fit (calibration) split**, ties broken towards `--base-threshold`.
* `fit_split.min_eer` — the classic speaker-verification operating point (FAR == FRR).
* `holdout.*` — every global operating point re-scored on the held-out split.
* `verdict` — adaptive vs the **best** single global threshold
  (`adaptive_better` / `global_better` / `tie`, band `--tolerance`).
* `verdict_vs_deployed_base` — adaptive vs the currently deployed global threshold,
  which answers "is the current config already good enough?".

## Known limitations

* **Per-device thresholds are keyed on the *matched* device, not the capture device.**
  `verify.py` records the outcome against `selected_record["device_id"]`, so a clip from
  a never-enrolled mic inherits the calibration of whichever enrolled voiceprint it
  matched. The report groups rows by capture device but `threshold_used` follows the
  matched device; both are in `cross_mic_trials.csv` (`device_id` and
  `matched_device_id`). Expect cross-device rows to share a `meanThr`.
* With the deployed base threshold of `0.60` and `adaptive_threshold_min = 0.6`, the
  adaptive threshold can only *tighten* — it can never drop below the base. Lowering
  the bar for a marginal device requires `adaptive_threshold_min < base`.
* `impostor` clips are verified against the target speaker, matching how the service
  handles an auth attempt for one user id.
* The harness disables the Neo4j historical-candidate fallback, so `fallback_used` is
  always false here; a deployment relying on candidate search should re-check separately.
* **The 2025 dashcam corpus is a KNOWN DIFFERENT SPEAKER** (see PLAN.md "Current
  State": 0.28 vs 0.88+ for live voice). Its clips are legitimate *impostor* trials
  (`speaker: dashcam_copilot`, `device_id: dashcam-2025`) and must never be labelled as
  `scott` or mixed into a same-speaker eval set — doing so silently poisons FAR/FRR.
* No labelled multi-device corpus exists in-repo yet, so no real numbers have been
  produced; everything so far has been verified against synthetic embeddings.

## Sample output (synthetic fixture, base threshold 0.60)

```
CROSS-MIC SPEAKER VERIFICATION REPORT
  schema            : sophia.cross_mic_eval.v1
  target speaker    : scott
  embedding backend : synthetic (model_loaded=None)
  embeddings cache  : embeddings.json (cache-only run, hits=33, misses=0)
  dataset           : 33 clips, 3 speakers, devices=car-mic,dashcam-2025,kitchen-speaker,laptop,phone
  splits            : enroll=6, calib=10, eval=17
  enrolled devices  : laptop,phone
  trials scored     : 17 (target=11, impostor=6, cross-device=3, same-device=8)

POOLED (reported split)
  effective/adaptive    : FAR=0.0000 FRR=0.0000 BACC=1.0000 ACC=1.0000 accept=0.6471
  global@base_global    : FAR=0.5000 FRR=0.0000 BACC=0.7500 ACC=0.8235 accept=0.8235
  AUC / EER             : AUC=1.0000 EER=0.0000

PER DEVICE
  device              n  tgt  imp  accept     FAR     FRR    BACC     AUC  meanThr
  car-mic             3    3    0  1.0000     n/a  0.0000     n/a     n/a   0.7361
  dashcam-2025        3    0    3  0.0000  0.0000     n/a     n/a     n/a   0.7361

THRESHOLD SWEEP (fit=calib, n_fit=10, n_holdout=17)
  best global threshold on fit split : 0.7902
  holdout global_fitted       : BACC=1.0000 FAR=0.0000 FRR=0.0000 adaptive-BACC=0.0000
  VERDICT vs best single global  : tie (compared_against=global_fitted)
  VERDICT vs deployed base       : adaptive_better

WARNINGS
  ! embeddings came from a synthetic cache (not ECAPA-TDNN): accuracy figures are
    pipeline smoke-test output, NOT measured speaker-verification accuracy
```

Read this as "the harness works and its fields move", not as a result: the close
impostor (`dashcam_copilot`) is what drives the 0.5 FAR at the global 0.60, and the
adaptive thresholds raise the bar to 0.74 for the device it matched.

## Tests

`voice-agent/tests/test_verification_eval.py` covers the scoring/reporting maths with
synthetic scores (no audio, no model download): confusion matrix, accept-on-equality
parity with `verify.py`, AUC with mid-ranks, ROC corners, EER, threshold fitting and
tie-breaks, manifest parsing, cache round-trip, backend gating, calibration freezing,
the full `run_evaluation` pass, and the CLI (report written / manifest error / skip).
