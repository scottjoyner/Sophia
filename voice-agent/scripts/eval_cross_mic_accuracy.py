#!/usr/bin/env python3
"""Measure cross-microphone speaker-verification accuracy (PLAN.md Phase 6 gap).

Given a labelled manifest of clips (JSONL/CSV: ``path``, ``speaker``/``user_id``,
``device_id``, optional ``split``), this harness enrols per-device voiceprints,
seeds the adaptive per-device thresholds from a calibration split, and then scores
held-out clips through the *service* verification path
(``VoiceprintRegistry`` + ``auth.verify.verify_audio_segment``).

It reports per-device accept rate, FAR/FRR at the effective threshold, pooled and
per-device ROC/AUC, the confusion matrix at the default 0.60 threshold, and a
threshold sweep answering "does the adaptive per-device threshold beat one global
threshold on held-out data?".

Examples:

    # full run (needs speechbrain + torch for ECAPA-TDNN embeddings)
    python scripts/eval_cross_mic_accuracy.py \\
        --manifest datasets/cross_mic/manifest.jsonl --out-dir reports/cross_mic

    # compute embeddings once, then re-run threshold sweeps with no model and no audio
    python scripts/eval_cross_mic_accuracy.py --manifest m.jsonl --out-dir reports/run1 \\
        --embeddings-cache reports/embeddings.json
    python scripts/eval_cross_mic_accuracy.py --manifest m.jsonl --out-dir reports/run2 \\
        --embeddings-cache reports/embeddings.json --base-threshold 0.55

    # labelled multi-device clips where the manifest already fixes the split
    python scripts/eval_cross_mic_accuracy.py --manifest m.csv --out-dir reports/run3 \\
        --target-speaker scott

Exit codes: 0 report written, 1 unexpected failure, 2 usage/manifest error,
3 skipped (no ECAPA-TDNN backend and no usable embeddings cache).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from voice_agent.auth.verification_eval import (  # noqa: E402
    EmbeddingCache,
    EmbeddingUnavailable,
    ManifestError,
    format_summary,
    load_manifest,
    run_evaluation,
    skipped_report,
    trials_csv,
)
from voice_agent.config import load_config  # noqa: E402

REPORT_NAME = "cross_mic_report.json"
SUMMARY_NAME = "cross_mic_summary.txt"
TRIALS_NAME = "cross_mic_trials.csv"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cross-microphone speaker-verification accuracy harness.",
    )
    parser.add_argument("--manifest", required=True, help="Labelled clips manifest (JSONL/JSON/CSV).")
    parser.add_argument("--out-dir", required=True, help="Directory for the JSON report + summary.")
    parser.add_argument("--config", default=None, help="Path to a YAML config file.")
    parser.add_argument("--artifacts-dir", default=None, help="Override artifacts dir from config.")
    parser.add_argument(
        "--target-speaker",
        default=None,
        help="Speaker to enrol + verify against (default: the speaker with most enroll clips).",
    )
    parser.add_argument("--base-threshold", type=float, default=0.60, help="Global/base threshold.")
    parser.add_argument(
        "--report-threshold", type=float, default=0.60, help="Threshold for the confusion matrix."
    )
    parser.add_argument("--holdout-ratio", type=float, default=0.34, help="Eval share of unsplit clips.")
    parser.add_argument("--calib-ratio", type=float, default=0.33, help="Calibration share of unsplit clips.")
    parser.add_argument("--seed", type=int, default=0, help="Split shuffle seed.")
    parser.add_argument("--tolerance", type=float, default=0.01, help="Balanced-accuracy tie band.")
    parser.add_argument(
        "--adaptive-threshold",
        choices=("enabled", "disabled"),
        default=None,
        help="Override AuthConfig.adaptive_threshold_enabled (default: use config value).",
    )
    parser.add_argument(
        "--embeddings-cache",
        default=None,
        help="JSON cache of clip embeddings (read/write); enables model-free threshold sweeps.",
    )
    parser.add_argument(
        "--write-embeddings-cache",
        default=None,
        help="Write the synthetic/derived embeddings to this path even when reading another cache.",
    )
    parser.add_argument(
        "--allow-fallback-embeddings",
        action="store_true",
        help="Run on SpeakerEmbedder's statistic fallback when ECAPA-TDNN is unavailable (results are NOT accuracy).",
    )
    parser.add_argument("--no-sweep", action="store_true", help="Skip the global-vs-adaptive comparison.")
    parser.add_argument("--keep-work", action="store_true", help="Keep the scratch artifacts dir.")
    parser.add_argument("--json", action="store_true", help="Print the JSON report instead of the summary.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = out_dir / ".work"

    try:
        records = load_manifest(Path(args.manifest))
    except ManifestError as exc:
        print(f"Manifest error: {exc}", file=sys.stderr)
        return 2

    config = load_config(args.config)
    if args.artifacts_dir:
        config.paths = config.paths.model_copy(update={"artifacts_dir": args.artifacts_dir})
    cache = EmbeddingCache.load(Path(args.embeddings_cache)) if args.embeddings_cache else None

    try:
        report = run_evaluation(
            records,
            config=config,
            work_dir=work_dir,
            base_threshold=args.base_threshold,
            report_threshold=args.report_threshold,
            target_speaker=args.target_speaker,
            cache=cache,
            allow_fallback_embeddings=args.allow_fallback_embeddings,
            adaptive_enabled=None if args.adaptive_threshold is None else args.adaptive_threshold == "enabled",
            run_sweep=not args.no_sweep,
            tolerance=args.tolerance,
            seed=args.seed,
            holdout_ratio=args.holdout_ratio,
            calib_ratio=args.calib_ratio,
        )
    except EmbeddingUnavailable as exc:
        report = skipped_report(str(exc), manifest=str(args.manifest))
        (out_dir / REPORT_NAME).write_text(json.dumps(report, indent=2, default=str))
        (out_dir / SUMMARY_NAME).write_text(format_summary(report) + "\n")
        print(format_summary(report), file=sys.stderr)
        print(f"skipped report written to {out_dir / REPORT_NAME}", file=sys.stderr)
        return 3
    except ManifestError as exc:
        print(f"Manifest error: {exc}", file=sys.stderr)
        return 2

    if cache is not None:
        target_cache = Path(args.write_embeddings_cache) if args.write_embeddings_cache else cache.path
        if target_cache != cache.path:
            cache.path = target_cache
        if cache.hits + len(cache.entries) > 0:
            cache.save()
    if not args.keep_work:
        shutil.rmtree(work_dir, ignore_errors=True)

    report["inputs"] = {
        "manifest": str(Path(args.manifest).resolve()),
        "n_manifest_clips": len(records),
        "out_dir": str(out_dir.resolve()),
    }
    (out_dir / REPORT_NAME).write_text(json.dumps(report, indent=2, default=str))
    (out_dir / SUMMARY_NAME).write_text(format_summary(report) + "\n")
    (out_dir / TRIALS_NAME).write_text(
        trials_csv(list(report.get("calibration_trials") or []) + list(report.get("trials") or []))
    )
    print(json.dumps(report, indent=2, default=str) if args.json else format_summary(report))
    print(f"\nreport : {out_dir / REPORT_NAME}\nsummary: {out_dir / SUMMARY_NAME}\ntrials : {out_dir / TRIALS_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
