#!/usr/bin/env python3
"""TTS quality evaluation against a live voice reference (PLAN.md Phase 8, item 3).

There is no TTS quality harness in the repo today: ``tts/openvoice_tts.py`` and
``tts/coqui_tts.py`` synthesize, ``tts/pyttsx3_fallback.py`` emits a 440 Hz
placeholder tone, and nothing ever scores the output. This script adds the
missing scoring pass, with three hard rules so it cannot produce fake quality
numbers:

1. **No real backend, no evaluation.** When the configured backend silently falls
   back to ``FallbackTTS`` (a tone generator), the run is reported ``skipped``
   with the reason. ``--allow-placeholder-backend`` will still run, but every
   quality metric is then reported as ``SKIPPED`` rather than computed from a
   tone.
2. **No reference, no identity metric.** Speaker similarity is only computed
   against ``--reference-wav`` / ``$SOPHIA_TTS_REFERENCE_WAV``. Without it the
   field says so instead of substituting a self-comparison.
3. **Human ratings are placeholders.** There is no MOS estimator here. The MOS
   field is the literal string ``"PLACEHOLDER - requires a human listening panel"``
   and stays that way until someone runs one.

What IS measured, and what each number means:

* ``wer`` -- word error rate from faster-whisper transcribing the synthesized
  audio against the phrase text. Objective intelligibility. Needs
  ``faster-whisper`` + cached weights; otherwise ``SKIPPED`` with the reason.
* ``speaker_similarity_to_reference`` -- cosine similarity between ECAPA-TDNN
  embeddings of the synthesized audio and the reference clip. Identity fidelity.
  Needs ``speechbrain`` + ``torch``; otherwise ``SKIPPED`` with the reason.
* ``spectral_distance_db_to_reference`` -- mean absolute difference between
  frame-averaged log-magnitude spectra. **Descriptive only**: it says "this does
  not sound like the reference's spectral shape", it is not a quality score.
* ``duration_s`` / ``chars_per_s`` / ``rms_dbfs`` / ``peak_dbfs`` /
  ``clipped_fraction`` / ``silence_trim_ms`` -- mechanical signal facts, exact
  and backend-independent.

Examples::

    python scripts/tts_quality_eval.py \
        --config configs/container.yaml \
        --reference-wav /data/voice-insight/reference/scott_phone_10s.wav \
        --out-dir ../reports/phase8/tts

    # intelligibility only, no identity claim
    python scripts/tts_quality_eval.py --reference-wav ref.wav --out-dir out --no-speaker-similarity

Exit codes: 0 report written, 1 unexpected failure, 2 usage error,
3 skipped (placeholder backend, or no reference clip).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from voice_agent.config import load_config  # noqa: E402
from voice_agent.server.pipelines import PipelineManager  # noqa: E402
from voice_agent.util.audio import read_wav, write_wav  # noqa: E402

REPORT_SCHEMA = "sophia.tts_quality_eval.v1"
PLACEHOLDER_BACKEND = "FallbackTTS"
MOS_PLACEHOLDER = "PLACEHOLDER - requires a human listening panel"
DEFAULT_PHRASES = (
    "Sophia, what is on my calendar tomorrow morning?",
    "Set a reminder to call the workshop at four o'clock.",
    "Read back the last action item from this morning's meeting.",
    "How many open tasks are assigned to me right now?",
)
_SILENCE_FLOOR = 0.005


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _skipped(reason: str, fix: list[str], detail: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "status": "skipped",
        "skip_reason": reason,
        "fix": fix,
        "generated_at": _now(),
        "detail": detail or {},
        "metrics": {},
        "warnings": [],
        "phase8_evidence": False,
        "phase8_evidence_note": (
            "a skipped run is never Phase 8 evidence; it needs a real TTS backend, a live voice "
            "reference, real ASR and ECAPA embeddings"
        ),
        "reliability_note": (
            "No TTS quality figure was produced. The harness does not substitute placeholder scores."
        ),
    }


def _load_phrases(raw: str | None, limit: int | None) -> list[str]:
    if raw:
        path = Path(raw)
        if not path.exists():
            raise ValueError(f"phrase file not found: {path}")
        phrases = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        phrases = list(DEFAULT_PHRASES)
    return phrases[: limit or len(phrases)]


def _rms_dbfs(samples: np.ndarray) -> float | None:
    if samples.size == 0:
        return None
    rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float64)))))
    if rms <= 0:
        return None
    return round(20.0 * math.log10(rms), 2)


def _signal_stats(samples: np.ndarray, sample_rate: int, text: str) -> dict[str, Any]:
    signal = np.asarray(samples, dtype=np.float32)
    duration_s = signal.size / float(sample_rate or 1)
    peak = float(np.max(np.abs(signal))) if signal.size else 0.0
    voiced = np.abs(signal) >= _SILENCE_FLOOR
    leading_ms = 0.0
    trailing_ms = 0.0
    if voiced.any():
        first = int(np.argmax(voiced))
        last = int(signal.size - np.argmax(voiced[::-1]) - 1)
        leading_ms = round(first / sample_rate * 1000, 1)
        trailing_ms = round((signal.size - 1 - last) / sample_rate * 1000, 1)
    return {
        "text": text,
        "duration_s": round(duration_s, 3),
        "chars_per_s": round(len(text) / duration_s, 2) if duration_s else None,
        "rms_dbfs": _rms_dbfs(signal),
        "peak_dbfs": round(20.0 * math.log10(peak), 2) if peak > 0 else None,
        "clipped_fraction": round(float(np.mean(np.abs(signal) >= 0.999)), 6),
        "leading_silence_ms": leading_ms,
        "trailing_silence_ms": trailing_ms,
        "silent": bool(signal.size == 0 or peak < _SILENCE_FLOOR),
    }


def _log_spectrum(samples: np.ndarray, sample_rate: int, bands: int = 40) -> np.ndarray:
    """Frame-averaged log-magnitude spectrum, coarse-banded and L2-normalised.

    Deliberately dependency-light (numpy only): this is a shape comparison, not a
    perceptual model. It is reported as ``descriptive`` and never as a score.
    """
    signal = np.asarray(samples, dtype=np.float32)
    if signal.size == 0:
        return np.zeros(bands, dtype=float)
    window = min(1024, max(64, signal.size))
    frames = max(1, signal.size // window)
    averaged = np.zeros(window // 2 + 1, dtype=float)
    for index in range(frames):
        chunk = signal[index * window : (index + 1) * window]
        if chunk.size < 8:
            continue
        spectrum = np.abs(np.fft.rfft(chunk * np.hanning(chunk.size)))
        averaged[: spectrum.size] += spectrum
    averaged /= max(1, frames)
    log_mag = 20.0 * np.log10(averaged + 1e-10)
    edges = np.linspace(0, log_mag.size, bands + 1).astype(int)
    coarse = np.array([log_mag[a:b].mean() if b > a else 0.0 for a, b in zip(edges[:-1], edges[1:], strict=False)])
    norm = float(np.linalg.norm(coarse))
    return coarse / norm if norm else coarse


def _spectral_distance_db(reference: np.ndarray, candidate: np.ndarray) -> float | None:
    if reference.size != candidate.size or reference.size == 0:
        return None
    return round(float(np.mean(np.abs(reference - candidate))), 2)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _wer(reference: str, hypothesis: str) -> float | None:
    """Standard word error rate: Levenshtein(word substitutions/deletions/insertions) / |reference|."""
    ref = _words(reference)
    hyp = _words(hypothesis)
    if not ref:
        return None
    previous = list(range(len(hyp) + 1))
    for i, ref_word in enumerate(ref, start=1):
        current = [i] + [0] * len(hyp)
        for j, hyp_word in enumerate(hyp, start=1):
            current[j] = min(
                previous[j] + 1,  # deletion
                current[j - 1] + 1,  # insertion
                previous[j - 1] + (ref_word != hyp_word),  # substitution
            )
        previous = current
    return round(previous[-1] / len(ref), 4)


def _asr_transcribe(samples: np.ndarray, sample_rate: int, config: Any) -> tuple[str | None, str]:
    """Transcribe with faster-whisper. Returns ``(text, reason_when_unavailable)``."""
    if importlib.util.find_spec("faster_whisper") is None:
        return None, "faster-whisper is not installed (pip install 'voice-agent[insight]')"
    try:
        from voice_agent.stt.faster_whisper_batch import _get_model

        model = _get_model(config.stt)
        if model is None:
            return None, "faster-whisper model could not be loaded"
        if config.stt.sample_rate and sample_rate != config.stt.sample_rate:
            from scipy import signal

            target = int(round(samples.size * config.stt.sample_rate / sample_rate))
            samples = signal.resample(samples, target).astype(np.float32)
        segments, _info = model.transcribe(samples, language="en")
        return " ".join(segment.text.strip() for segment in segments).strip(), ""
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _speaker_similarity(samples: np.ndarray, sample_rate: int, embedder: Any, reference: Any) -> float | None:
    candidate = np.asarray(embedder.embed(samples, sample_rate), dtype=float)
    denom = float(np.linalg.norm(candidate) * np.linalg.norm(reference))
    if denom == 0:
        return None
    return round(float(np.dot(candidate, reference) / denom), 4)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TTS quality evaluation against a live voice reference.")
    parser.add_argument("--config", default=None, help="Sophia YAML config (default: load_config(None)).")
    parser.add_argument("--out-dir", required=True, help="Directory for the report, summary and rendered wavs.")
    parser.add_argument(
        "--reference-wav",
        default=None,
        help="Live voice reference clip (default: $SOPHIA_TTS_REFERENCE_WAV).",
    )
    parser.add_argument("--phrases", default=None, help="File with one evaluation phrase per line.")
    parser.add_argument("--max-phrases", type=int, default=None, help="Only use the first N phrases.")
    parser.add_argument("--allow-placeholder-backend", action="store_true", help="Run even on the tone placeholder (metrics stay SKIPPED).")
    parser.add_argument("--no-asr", action="store_true", help="Skip the word-error-rate pass.")
    parser.add_argument("--no-speaker-similarity", action="store_true", help="Skip the ECAPA identity pass.")
    parser.add_argument("--json", action="store_true", help="Print the JSON report instead of the summary.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    wav_dir = out_dir / "wav"
    wav_dir.mkdir(parents=True, exist_ok=True)

    config = load_config(args.config)
    manager = PipelineManager(config, out_dir / "scratch")
    backend_class = type(manager.tts).__name__
    warnings: list[str] = []
    backend_detail = {
        "configured_backend": config.tts.backend,
        "loaded_class": backend_class,
        "voice": config.tts.voice,
        "speaker_wav": config.tts.speaker_wav,
        "use_gpu": config.tts.use_gpu,
        "real_backend": backend_class != PLACEHOLDER_BACKEND,
    }
    measured = backend_detail["real_backend"]
    if not measured and not args.allow_placeholder_backend:
        report = _skipped(
            f"configured tts backend {config.tts.backend!r} resolved to {backend_class}, the offline "
            "placeholder that emits a 440 Hz tone; scoring it would produce meaningless numbers",
            [
                "pip install -e '.[tts-openvoice]' torch torchaudio   # or '.[tts-coqui]'",
                "set tts.backend: openvoice and tts.openvoice_model_path: <OpenVoice v2 checkout>",
                "set tts.speaker_wav: <live voice reference clip>",
                "re-run with --allow-placeholder-backend to prove the plumbing only",
            ],
            detail=backend_detail,
        )
        return _emit(report, args, out_dir)

    reference_raw = args.reference_wav or os.environ.get("SOPHIA_TTS_REFERENCE_WAV", "")
    reference_samples: np.ndarray | None = None
    reference_rate = 0
    reference_spectrum = None
    if reference_raw:
        reference_path = Path(reference_raw)
        if not reference_path.exists():
            report = _skipped(
                f"reference clip {reference_path} does not exist",
                ["record or export a live voice clip and pass --reference-wav <path>"],
                detail=backend_detail,
            )
            return _emit(report, args, out_dir)
        reference_samples, reference_rate = read_wav(str(reference_path))
        reference_spectrum = _log_spectrum(reference_samples, reference_rate)
    else:
        reference_path = None
        warnings.append(
            "no --reference-wav / $SOPHIA_TTS_REFERENCE_WAV: identity and spectral-reference metrics are skipped"
        )

    try:
        phrases = _load_phrases(args.phrases, args.max_phrases)
    except ValueError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return 2
    if not phrases:
        report = _skipped(
            "no evaluation phrases available",
            ["pass --phrases <file> with one phrase per line"],
            detail=backend_detail,
        )
        return _emit(report, args, out_dir)

    embedder = None
    embedder_reason = "skipped by --no-speaker-similarity"
    speaker_similarity_asked = not args.no_speaker_similarity and reference_samples is not None
    if speaker_similarity_asked:
        try:
            from voice_agent.auth.speaker_embedder import SpeakerEmbedder

            embedder = SpeakerEmbedder()
            if embedder.model is None:
                embedder_reason = (
                    "ECAPA-TDNN could not be loaded (speechbrain + torch missing); the statistic "
                    "fallback carries no speaker identity so no similarity is reported"
                )
                embedder = None
        except Exception as exc:
            embedder_reason = f"{type(exc).__name__}: {exc}"
    elif reference_samples is None:
        embedder_reason = "no reference clip provided"
    reference_embedding = None
    if embedder is not None and reference_samples is not None:
        reference_embedding = np.asarray(
            embedder.embed(reference_samples, reference_rate), dtype=float
        )

    per_phrase: list[dict[str, Any]] = []
    failures: list[str] = []
    asr_reason = "skipped by --no-asr" if args.no_asr else ""
    if not args.no_asr:
        asr_reason = "no ASR transcription available for the rendered phrases"
    spectral_reason = ""
    if not measured:
        asr_reason = f"backend is the {PLACEHOLDER_BACKEND} tone placeholder"
        spectral_reason = f"backend is the {PLACEHOLDER_BACKEND} tone placeholder"
        embedder_reason = f"backend is the {PLACEHOLDER_BACKEND} tone placeholder"
    elif reference_spectrum is None:
        spectral_reason = "no reference clip provided"
    for index, phrase in enumerate(phrases):
        started = time.perf_counter()
        try:
            audio = manager.tts.synthesize(phrase)
        except Exception as exc:
            failures.append(f"{type(exc).__name__}: {exc}")
            per_phrase.append({"text": phrase, "error": f"{type(exc).__name__}: {exc}"})
            continue
        samples = np.asarray(audio, dtype=np.float32)
        sample_rate = int(getattr(manager.tts, "sample_rate", 16000) or 16000)
        path = wav_dir / f"phrase_{index:02d}.wav"
        write_wav(path, samples, sample_rate)
        row = _signal_stats(samples, sample_rate, phrase)
        row.update({"wav": str(path), "synthesis_ms": round((time.perf_counter() - started) * 1000, 1)})

        if backend_detail["real_backend"]:
            if reference_spectrum is not None:
                row["spectral_distance_db_to_reference"] = _spectral_distance_db(
                    reference_spectrum, _log_spectrum(samples, sample_rate)
                )
                row["spectral_distance_note"] = "descriptive only; not a quality score"
            if not args.no_asr:
                text, reason = _asr_transcribe(samples, sample_rate, config)
                row["asr_transcript"] = text
                row["wer"] = _wer(phrase, text) if text else None
                if text is None:
                    row["wer_reason"] = reason
            if reference_embedding is not None:
                row["speaker_similarity_to_reference"] = _speaker_similarity(
                    samples, sample_rate, embedder, reference_embedding
                )
        per_phrase.append(row)

    scored = [row for row in per_phrase if "duration_s" in row]
    wers = [row["wer"] for row in scored if row.get("wer") is not None]
    similarities = [row["speaker_similarity_to_reference"] for row in scored if row.get("speaker_similarity_to_reference") is not None]
    distances = [row["spectral_distance_db_to_reference"] for row in scored if row.get("spectral_distance_db_to_reference") is not None]

    if not measured:
        warnings.append(
            f"{PLACEHOLDER_BACKEND} produced these files: they are a 440 Hz tone placeholder, so every "
            "quality metric is reported as SKIPPED and none of them is a TTS measurement"
        )

    metrics: dict[str, Any] = {
        "n_phrases": len(phrases),
        "n_rendered": len(scored),
        "n_failed": len(failures),
        "wer_mean": (
            round(sum(wers) / len(wers), 4)
            if wers
            else "SKIPPED: " + (asr_reason if asr_reason else "no ASR transcription available")
        ),
        "speaker_similarity_mean": (
            round(sum(similarities) / len(similarities), 4)
            if similarities
            else "SKIPPED: " + (embedder_reason if embedder_reason else "not computed")
        ),
        "spectral_distance_db_mean": (
            round(sum(distances) / len(distances), 2)
            if distances
            else "SKIPPED: " + (spectral_reason if spectral_reason else "not computed")
        ),
        "mos": MOS_PLACEHOLDER,
        "mos_note": "no MOS estimator exists in this repo; a human listening panel must fill this in",
    }

    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "status": "ok" if measured else "degraded",
        "skip_reason": None,
        "reason": (
            "rendered the configured backend and scored it against the reference clip"
            if measured
            else f"ran on the {PLACEHOLDER_BACKEND} placeholder under --allow-placeholder-backend"
        ),
        "generated_at": _now(),
        "detail": {
            "backend": backend_detail,
            "config": str(args.config),
            "reference_wav": str(reference_path) if reference_samples is not None else None,
            "reference_duration_s": (
                round(reference_samples.size / float(reference_rate), 3)
                if reference_samples is not None and reference_rate
                else None
            ),
            "asr_enabled": not args.no_asr,
            "speaker_similarity_requested": speaker_similarity_asked,
            "speaker_similarity_skip_reason": embedder_reason if embedder is None else None,
            "failures": failures,
        },
        "metrics": metrics,
        "phrases": per_phrase,
        "warnings": warnings,
        "fix": [],
        "phase8_evidence": bool(measured and wers and similarities),
        "phase8_evidence_note": (
            "phase8_evidence is true only when a real backend rendered audio, an ASR produced a WER and "
            "ECAPA produced a reference similarity; PLAN.md stays unchecked otherwise"
        ),
    }
    return _emit(report, args, out_dir)


def _emit(report: dict[str, Any], args: argparse.Namespace, out_dir: Path) -> int:
    path = out_dir / "tts_quality_report.json"
    path.write_text(json.dumps(report, indent=2, default=str))
    (out_dir / "tts_quality_summary.txt").write_text(_summary(report) + "\n")
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(_summary(report))
    print(f"\nreport : {path}")
    if report["status"] == "skipped":
        return 3
    if report["detail"].get("failures"):
        return 1
    return 0


def _summary(report: dict[str, Any]) -> str:
    lines = [
        "TTS QUALITY EVALUATION",
        f"  schema   : {report['schema']}",
        f"  status   : {report['status'].upper()}",
        f"  generated: {report.get('generated_at')}",
    ]
    if report.get("reason"):
        lines.append(f"  verdict  : {report['reason']}")
    if report.get("skip_reason"):
        lines.append(f"  skipped  : {report['skip_reason']}")
    backend = (report.get("detail") or {}).get("backend")
    if backend:
        lines.append(
            f"  backend  : configured={backend['configured_backend']} loaded={backend['loaded_class']} "
            f"real_backend={backend['real_backend']}"
        )
    for key, value in (report.get("metrics") or {}).items():
        lines.append(f"  {key:<24}: {value}")
    for warning in report.get("warnings") or []:
        lines.append(f"  ! {warning}")
    for command in report.get("fix") or []:
        lines.append(f"  -> {command}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
