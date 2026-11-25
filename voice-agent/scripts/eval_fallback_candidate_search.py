#!/usr/bin/env python3
"""Held-out evaluation of the Neo4j historical-candidate fallback (PLAN.md Phase 8, item 5).

``auth/verify.py`` verifies against the **active head** voiceprint first and, only
when that is below threshold, asks the graph for historical candidates
(``VoiceprintRegistry.get_historical_candidates`` ->
``VoiceprintGraphStore.search_candidates``: Neo4j vector index first, cosine scan
over every version/sample as the fallback). The cross-mic harness in
``scripts/eval_cross_mic_accuracy.py`` deliberately drops the Neo4j password, so
``fallback_used`` is always false there -- this script is the piece that was
missing.

It builds real version lineage in Neo4j (several enrolment generations per
device, each superseding the previous one), then scores held-out clips through
``verify_audio_segment`` and reports:

* ``rescues`` -- held-out **target** clips the active head rejected that the
  historical candidate search accepted (this is the whole point of the feature).
* ``harms`` -- held-out **impostor** clips the fallback accepted.
* ``fallback_fire_rate`` -- how often the fallback ran at all, so a run where the
  active head never fails cannot be mistaken for a passing evaluation.

Guards (this script never fabricates a result):

* ``--manifest`` is required; a manifest error exits 2.
* **Dashcam guard.** The 2025 dashcam corpus is a *known different speaker*
  (PLAN.md "Current State": 0.28 vs 0.88+ for live voice). Any manifest row that
  labels a dashcam-looking clip as the target speaker is rejected with exit 2,
  because mixing it into the same-speaker set silently poisons FAR/FRR. It is
  legitimate as an impostor (``speaker: dashcam_copilot``).
* No Neo4j password -> exit 3 ``skipped`` (the feature does not exist without the
  graph).
* No ECAPA-TDNN and no embeddings cache -> exit 3 ``skipped``. ``--allow-
  synthetic-embeddings`` runs a *mechanism* dry run instead, flagged
  ``status: degraded`` and ``phase8_evidence: false``.
* ``--force-active-head-degradation`` deliberately corrupts the active head after
  enrolment so the fallback is forced to fire. That is a mechanism probe, not a
  measurement, and the report says so.

Examples::

    # real held-out clips, real graph, real embeddings
    python scripts/eval_fallback_candidate_search.py \\
        --manifest datasets/fallback/manifest.jsonl \\
        --embeddings-cache reports/fallback_embeddings.json \\
        --out-dir ../reports/phase8/fallback

    # prove the code path works, no ECAPA needed (NOT accuracy)
    python scripts/eval_fallback_candidate_search.py \\
        --manifest datasets/fallback/manifest.jsonl \\
        --out-dir ../reports/phase8/fallback_dryrun \\
        --allow-synthetic-embeddings --force-active-head-degradation

Exit codes: 0 report written, 1 unexpected failure, 2 usage/manifest error
(including the dashcam guard), 3 skipped (Neo4j or embeddings unavailable).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from voice_agent.auth.enroll import _prune_outlier_embeddings  # noqa: E402
from voice_agent.auth.registry import VoiceprintRegistry  # noqa: E402
from voice_agent.auth.verification_eval import (  # noqa: E402
    ECAPA_MODEL_SOURCE,
    SPLIT_ENROLL,
    SPLIT_EVAL,
    ClipRecord,
    EmbeddingCache,
    EmbeddingUnavailable,
    ManifestError,
    _clip_digest,
    load_manifest,
    resolve_target_speaker,
    synthetic_embedding_cache,
)
from voice_agent.auth.verify import verify_audio_segment  # noqa: E402
from voice_agent.config import load_config  # noqa: E402
from voice_agent.util.time import now_ms  # noqa: E402

REPORT_SCHEMA = "sophia.fallback_candidate_eval.v1"
# The 2025 dashcam corpus is a confirmed different speaker; these patterns mark
# rows that must never be labelled as the target speaker in a manifest.
DASHCAM_MARKERS = ("dashcam", "dash_cam", "copilot", "2025_dashcam")
TRIALS_NAME = "fallback_trials.csv"
TRIAL_FIELDS = (
    "clip_id",
    "speaker",
    "device_id",
    "is_target",
    "primary_score",
    "fallback_score",
    "match_source",
    "fallback_used",
    "fallback_reason",
    "accepted",
    "auth_state",
    "threshold_used",
    "n_candidates",
    "best_candidate_id",
    "best_candidate_type",
)


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
        "reliability_note": (
            "No fallback figure was produced. The harness does not substitute placeholder scores."
        ),
    }


def assert_dashcam_not_target(records: Sequence[ClipRecord], target: str) -> None:
    """Refuse a manifest that labels the known-different 2025 dashcam speaker as the target."""
    offenders = []
    for record in records:
        haystack = f"{record.path} {record.clip_id} {record.device_id}".lower()
        if record.speaker == target and any(marker in haystack for marker in DASHCAM_MARKERS):
            offenders.append(f"{record.clip_id} ({record.path}) labelled speaker={record.speaker!r}")
    if not offenders:
        return
    listed = "\n  - ".join(offenders)
    raise ManifestError(
        "the 2025 dashcam corpus is a KNOWN DIFFERENT SPEAKER (PLAN.md 'Current State': 0.28 vs "
        "0.88+ for live voice) and must never appear in the same-speaker set for the target "
        f"speaker {target!r}:\n  - {listed}\n"
        "relabel those rows as an impostor (e.g. speaker=dashcam_copilot, device_id=dashcam-2025) "
        "or remove them."
    )


def _resolve_embeddings(
    records: Sequence[ClipRecord],
    cache: EmbeddingCache | None,
    allow_synthetic: bool,
) -> tuple[Callable[[ClipRecord], list[float]], dict[str, Any], list[str]]:
    """Return ``(embeddings_fn, backend_detail, warnings)`` or raise ``EmbeddingUnavailable``."""
    warnings: list[str] = []
    backend_detail: dict[str, Any] = {
        "model_loaded": None,
        "source": (cache.provenance().get("model") if cache else None),
        "embeddings_cache": str(cache.path) if cache else None,
        "cache_hits": 0,
        "cache_misses": 0,
        "synthetic_embeddings": bool(cache and cache.provenance().get("source") == "synthetic"),
    }
    if cache is not None and cache.entries:
        if backend_detail["synthetic_embeddings"]:
            warnings.append(
                "embeddings came from a synthetic cache (not ECAPA-TDNN): the fallback mechanism is "
                "exercised but the rescue/harm rates are NOT speaker-verification accuracy"
            )
        return _cache_only_embeddings(cache, backend_detail), backend_detail, warnings

    try:
        from voice_agent.auth.speaker_embedder import SpeakerEmbedder
    except Exception as exc:  # pragma: no cover - import guard
        raise EmbeddingUnavailable(f"SpeakerEmbedder could not be imported: {exc}") from exc
    embedder = SpeakerEmbedder()
    backend_detail["model_loaded"] = embedder.model is not None
    backend_detail["source"] = ECAPA_MODEL_SOURCE if embedder.model is not None else "statistic-fallback"
    if embedder.model is None:
        if not allow_synthetic:
            raise EmbeddingUnavailable(
                "ECAPA-TDNN is unavailable (speechbrain + torch missing) and no embeddings cache was "
                "supplied; rerun with --embeddings-cache <cache> after one real run, or with "
                "--allow-synthetic-embeddings for a mechanism dry run"
            )
        warnings.append(
            "no ECAPA-TDNN: embeddings were synthesised, so rescue/harm rates are pipeline smoke-test "
            "output and NOT speaker-verification accuracy"
        )
        synthetic = synthetic_embedding_cache(records, Path(cache.path if cache else "unused"), seed=11)
        cache = synthetic
        backend_detail["synthetic_embeddings"] = True
        backend_detail["embeddings_cache"] = str(synthetic.path)
        return _cache_only_embeddings(synthetic, backend_detail), backend_detail, warnings
    if cache is None:
        raise EmbeddingUnavailable("no embeddings cache supplied for a cache-free run is not supported")
    return _live_embeddings(embedder, cache, backend_detail), backend_detail, warnings


def _cache_only_embeddings(cache: EmbeddingCache, backend_detail: dict[str, Any]) -> Callable[[ClipRecord], list[float]]:
    def embeddings(clip: ClipRecord) -> list[float]:
        digest = _clip_digest(clip)
        cached = cache.get(digest)
        if cached is None:
            raise EmbeddingUnavailable(
                f"clip {clip.clip_id} is not in the embeddings cache ({clip.path}) and no model is "
                "available to embed it; run once with a real backend to build the cache"
            )
        return cached

    def counted(clip: ClipRecord) -> list[float]:
        value = embeddings(clip)
        backend_detail["cache_hits"] = cache.hits
        backend_detail["cache_misses"] = cache.misses
        return value

    return counted


def _live_embeddings(
    embedder: Any,
    cache: EmbeddingCache,
    backend_detail: dict[str, Any],
) -> Callable[[ClipRecord], list[float]]:
    from voice_agent.util.audio import read_wav

    def embeddings(clip: ClipRecord) -> list[float]:
        digest = _clip_digest(clip)
        cached = cache.get(digest)
        if cached is not None:
            backend_detail["cache_hits"] = cache.hits
            backend_detail["cache_misses"] = cache.misses
            return cached
        try:
            audio, sample_rate = read_wav(clip.path)
        except Exception as exc:
            raise EmbeddingUnavailable(f"clip {clip.clip_id} could not be read ({clip.path}): {exc}") from exc
        vector = [float(value) for value in embedder.embed(audio, sample_rate)]
        cache.put(digest, vector, clip)
        cache.metadata = {"source": "speaker_embedder", "model": ECAPA_MODEL_SOURCE, "dim": len(vector)}
        backend_detail["cache_hits"] = cache.hits
        backend_detail["cache_misses"] = cache.misses
        return vector

    return embeddings


def _chunks(items: Sequence[ClipRecord], generations: int) -> list[list[ClipRecord]]:
    if generations <= 1:
        return [list(items)]
    size = max(1, (len(items) + generations - 1) // generations)
    return [list(items[index : index + size]) for index in range(0, len(items), size)] or [[]]


def _enrol_generations(
    records: Sequence[ClipRecord],
    *,
    registry: VoiceprintRegistry,
    embeddings: Callable[[ClipRecord], list[float]],
    base_threshold: float,
    generations: int,
) -> list[dict[str, Any]]:
    """Enrol the target speaker per device as ``generations`` successive versions.

    Each ``save_device`` call supersedes the previous version, which is exactly how
    real re-enrolment builds lineage -- and what leaves historical candidates for
    the fallback search to find.
    """
    grouped: dict[tuple[str, str], list[ClipRecord]] = {}
    for record in records:
        grouped.setdefault((record.speaker, record.device_id), []).append(record)
    lineage: list[dict[str, Any]] = []
    for key in sorted(grouped):
        clips = sorted(grouped[key], key=lambda item: item.clip_id)
        for index, generation in enumerate(_chunks(clips, generations), start=1):
            vectors: list[list[float]] = []
            samples_meta: list[dict[str, Any]] = []
            for clip in generation:
                vectors.append(embeddings(clip))
                samples_meta.append(
                    {
                        "path": clip.path,
                        "clip_id": clip.clip_id,
                        "source": "fallback_candidate_eval",
                        "added_ts_ms": now_ms(),
                        "embedding": vectors[-1],
                    }
                )
            rejected: list[dict[str, Any]] = []
            if len(vectors) >= 3:
                vectors, samples_meta, rejected = _prune_outlier_embeddings(
                    vectors, samples_meta, reference=None, threshold=0.5
                )
            if not vectors:
                continue
            mean = np.mean(np.asarray(vectors, dtype=float), axis=0).tolist()
            saved = registry.save_device(
                key[0],
                key[1],
                mean,
                {
                    "samples": samples_meta,
                    "sample_count": len(samples_meta),
                    "append": False,
                    "source": "fallback_candidate_eval",
                    "updated_ts_ms": now_ms(),
                    "rejected_outliers": [item.get("clip_id") for item in rejected],
                },
                base_threshold,
                source="fallback_candidate_eval",
            )
            lineage.append(
                {
                    "speaker": key[0],
                    "device_id": key[1],
                    "generation": index,
                    "n_clips": len(generation),
                    "version_id": saved.get("version_id"),
                    "graph_saved": bool(saved.get("graph_saved")),
                    "graph_error": saved.get("graph_error"),
                }
            )
    return lineage


def _corrupt_active_head(
    registry: VoiceprintRegistry,
    user_id: str,
    device_id: str,
    impostor_vectors: Sequence[Sequence[float]],
    base_threshold: float,
) -> dict[str, Any] | None:
    """Replace the active head with an impostor centroid so the fallback must fire.

    Mechanism probe only: the resulting rescue/harm rates describe a deliberately
    broken head, not the deployed system.
    """
    vectors = [np.asarray(vector, dtype=float) for vector in impostor_vectors if vector]
    if not vectors:
        return None
    mean = np.mean(np.asarray(vectors, dtype=float), axis=0).tolist()
    saved = registry.save_device(
        user_id,
        device_id,
        mean,
        {
            "samples": [{"path": "<impostor-centroid>", "source": "fallback_candidate_eval_degradation"}],
            "sample_count": 1,
            "append": False,
            "source": "fallback_candidate_eval_degradation",
            "updated_ts_ms": now_ms(),
        },
        base_threshold,
        source="fallback_candidate_eval_degradation",
    )
    return {
        "applied": True,
        "device_id": device_id,
        "version_id": saved.get("version_id"),
        "graph_saved": bool(saved.get("graph_saved")),
        "note": "active head replaced with an impostor centroid; rescue/harm rates are a mechanism probe",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Held-out evaluation of the Neo4j historical-candidate fallback."
    )
    parser.add_argument("--manifest", required=True, help="Labelled held-out clips manifest (JSONL/JSON/CSV).")
    parser.add_argument("--out-dir", required=True, help="Directory for the JSON report + trials CSV.")
    parser.add_argument("--config", default=None, help="Path to a YAML config file.")
    parser.add_argument("--artifacts-dir", default=None, help="Override the scratch artifacts dir.")
    parser.add_argument("--target-speaker", default=None, help="Speaker to enrol + verify against.")
    parser.add_argument("--base-threshold", type=float, default=0.60, help="Threshold stored on every version.")
    parser.add_argument("--generations", type=int, default=2, help="Successive voiceprint versions per device.")
    parser.add_argument("--embeddings-cache", default=None, help="Embeddings cache to read and populate.")
    parser.add_argument("--allow-synthetic-embeddings", action="store_true", help="Mechanism dry run on synthetic vectors.")
    parser.add_argument(
        "--force-active-head-degradation",
        action="store_true",
        help="Corrupt the active head with an impostor centroid so the fallback is forced to fire (mechanism probe).",
    )
    parser.add_argument("--json", action="store_true", help="Print the JSON report instead of the summary.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_path = out_dir / "artifacts"

    try:
        records = load_manifest(Path(args.manifest))
    except ManifestError as exc:
        print(f"Manifest error: {exc}", file=sys.stderr)
        return 2
    target = resolve_target_speaker(records, args.target_speaker)
    try:
        assert_dashcam_not_target(records, target)
    except ManifestError as exc:
        print(f"Manifest rejected: {exc}", file=sys.stderr)
        return 2

    eval_clips = [record for record in records if record.split == SPLIT_EVAL] or [
        record for record in records if record.split != SPLIT_ENROLL
    ]
    if not eval_clips:
        print("Manifest error: no held-out (eval) clips to score", file=sys.stderr)
        return 2

    config = load_config(args.config)
    if not config.neo4j.password:
        report = _skipped(
            "no Neo4j password configured, so no historical candidate search exists to evaluate "
            "(auth/verify.py can only fall back to the graph)",
            [
                "export NEO4J_PASSWORD=<password>   # or set neo4j.password in the config / NEO4J_PASSWORD_FILE",
                "python scripts/ensure_neo4j_schema.py   # vector indexes must exist first",
                f"re-run: python scripts/eval_fallback_candidate_search.py --manifest {args.manifest} --out-dir {args.out_dir}",
            ],
            detail={
                "neo4j_uri": config.neo4j.uri,
                "neo4j_database": config.neo4j.database,
                "n_manifest_clips": len(records),
                "target_speaker": target,
            },
        )
        return _emit(report, args, out_dir)

    work_path.mkdir(parents=True, exist_ok=True)
    eval_config = config.model_copy(deep=True)
    eval_config.paths = config.paths.model_copy(
        update={"artifacts_dir": str(args.artifacts_dir or (work_path / "runs"))}
    )
    eval_config.auth = config.auth.model_copy(
        update={
            "threshold": float(args.base_threshold),
            "require_challenge": False,
            "adaptive_threshold_enabled": False,
        }
    )

    registry = VoiceprintRegistry(Path(eval_config.paths.artifacts_dir) / "results.sqlite", eval_config)
    if registry.graph is None:  # pragma: no cover - guarded by the password check above
        report = _skipped(
            "VoiceprintRegistry has no graph store despite a configured Neo4j password",
            ["check NEO4J_URI / NEO4J_USER / NEO4J_DATABASE and driver connectivity"],
        )
        return _emit(report, args, out_dir)

    try:
        registry.graph.ensure_schema()
    except Exception as exc:  # pragma: no cover - ensure_schema normally records schema_error instead
        report = _skipped(
            f"Neo4j schema bootstrap failed: {type(exc).__name__}: {exc}",
            [
                "export NEO4J_URI=bolt://127.0.0.1:7687   # host.docker.internal does not resolve on the host",
                "export NEO4J_PASSWORD=<password>   # auth failures surface here",
                "python scripts/ensure_neo4j_schema.py",
            ],
            detail={"neo4j_uri": eval_config.neo4j.uri, "neo4j_database": eval_config.neo4j.database},
        )
        return _emit(report, args, out_dir)
    schema_error = getattr(registry.graph, "schema_error", None)
    try:
        registry.graph.list_user_ids()
    except Exception as exc:
        schema_error = schema_error or f"{type(exc).__name__}: {exc}"
    if schema_error:
        report = _skipped(
            f"Neo4j is not usable for this run: {schema_error}",
            [
                "export NEO4J_URI=bolt://127.0.0.1:7687   # host.docker.internal does not resolve on the host",
                "export NEO4J_PASSWORD=<password>   # Unauthorized here means the password is wrong",
                "python scripts/ensure_neo4j_schema.py   # vector indexes must exist before candidate search",
            ],
            detail={"neo4j_uri": eval_config.neo4j.uri, "neo4j_database": eval_config.neo4j.database},
        )
        return _emit(report, args, out_dir)

    cache = (
        EmbeddingCache.load(Path(args.embeddings_cache))
        if args.embeddings_cache
        else EmbeddingCache(path=work_path / "embeddings.json")
    )
    try:
        embeddings, backend_detail, warnings = _resolve_embeddings(
            records, cache, args.allow_synthetic_embeddings
        )
    except EmbeddingUnavailable as exc:
        report = _skipped(
            str(exc),
            [
                "pip install -e '.[insight]'   # speechbrain + torch for ECAPA-TDNN",
                f"python scripts/eval_cross_mic_accuracy.py --manifest {args.manifest} --out-dir <dir> "
                f"--embeddings-cache {args.embeddings_cache or 'reports/embeddings.json'}",
            ],
            detail={"target_speaker": target, "n_holdout_clips": len(eval_clips)},
        )
        return _emit(report, args, out_dir)

    enrol_clips = [record for record in records if record.split == SPLIT_ENROLL] or eval_clips
    lineage = _enrol_generations(
        [record for record in enrol_clips if record.speaker == target],
        registry=registry,
        embeddings=embeddings,
        base_threshold=float(args.base_threshold),
        generations=max(1, int(args.generations)),
    )
    if not lineage:
        report = _skipped(
            f"no enrolment clips for target speaker {target!r}, so there is no voiceprint to fall back from",
            [f"add at least one row with speaker={target} and split=enroll to the manifest"],
            detail={"target_speaker": target, "speakers": sorted({r.speaker for r in records})},
        )
        return _emit(report, args, out_dir)

    degradation = None
    if args.force_active_head_degradation:
        impostor = [embeddings(record) for record in eval_clips if record.speaker != target]
        if not impostor:
            warnings.append(
                "--force-active-head-degradation needs held-out impostor clips to corrupt the active "
                "head with; the manifest has none, so the run stayed undegraded"
            )
        else:
            devices = sorted({entry["device_id"] for entry in lineage})
            degradation = [
                result
                for result in (
                    _corrupt_active_head(
                        registry,
                        target,
                        device_id,
                        impostor,
                        float(args.base_threshold),
                    )
                    for device_id in devices
                )
                if result
            ]
            if degradation:
                warnings.append(
                    "--force-active-head-degradation replaced the active head with an impostor centroid: "
                    "the rescue/harm rates below describe a deliberately broken head, not the deployed system"
                )

    trials: list[dict[str, Any]] = []
    for clip in eval_clips:
        payload = verify_audio_segment(
            eval_config,
            "fallback-candidate-eval",
            target,
            np.zeros(0, dtype=np.float32),
            16000,
            registry=registry,
            precomputed_embedding=embeddings(clip),
        )
        candidates = list(payload.get("voiceprint_candidates") or [])
        best = candidates[0] if candidates else {}
        trials.append(
            {
                "clip_id": clip.clip_id,
                "speaker": clip.speaker,
                "device_id": clip.device_id,
                "is_target": clip.speaker == target,
                "primary_score": payload.get("primary_score"),
                "fallback_score": payload.get("fallback_score"),
                "match_source": payload.get("match_source"),
                "fallback_used": bool(payload.get("fallback_used")),
                "fallback_reason": payload.get("fallback_reason"),
                "accepted": bool(payload.get("accepted")),
                "auth_state": payload.get("auth_state"),
                "threshold_used": payload.get("threshold_used"),
                "n_candidates": len(candidates),
                "best_candidate_id": best.get("candidate_id"),
                "best_candidate_type": best.get("candidate_type"),
            }
        )

    target_trials = [row for row in trials if row["is_target"]]
    impostor_trials = [row for row in trials if not row["is_target"]]
    fired = [row for row in trials if row["fallback_used"]]
    rescues = [row for row in target_trials if row["fallback_used"] and row["accepted"]]
    harms = [row for row in impostor_trials if row["fallback_used"] and row["accepted"]]
    missed = [row for row in target_trials if not row["fallback_used"] and not row["accepted"]]

    degraded = bool(backend_detail.get("synthetic_embeddings")) or bool(degradation)
    if not fired:
        warnings.append(
            "the fallback never fired on the held-out clips, so rescue/harm are 0 by construction and "
            "this run proves nothing about candidate search; add a harder held-out split or pass "
            "--force-active-head-degradation to probe the mechanism"
        )

    metrics: dict[str, Any] = {
        "n_holdout_trials": len(trials),
        "n_target": len(target_trials),
        "n_impostor": len(impostor_trials),
        "fallback_fire_rate": round(len(fired) / len(trials), 4) if trials else None,
        "rescues": len(rescues),
        "rescue_rate_of_target": round(len(rescues) / len(target_trials), 4) if target_trials else None,
        "harms": len(harms),
        "harm_rate_of_impostor": round(len(harms) / len(impostor_trials), 4) if impostor_trials else None,
        "target_rejected_without_fallback": len(missed),
        "candidate_coverage": (
            round(sum(1 for row in trials if row["n_candidates"] > 0) / len(trials), 4) if trials else None
        ),
        "accuracy_claim": (
            "NOT an accuracy measurement: embeddings are synthetic and/or the active head was "
            "deliberately degraded"
            if degraded
            else "measured on held-out clips with ECAPA-TDNN embeddings against a live Neo4j graph"
        ),
    }

    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "status": "degraded" if degraded else "ok",
        "skip_reason": None,
        "reason": (
            "held-out clips scored through the live graph candidate search"
            if not degraded
            else "mechanism run on synthetic embeddings and/or a deliberately degraded active head"
        ),
        "generated_at": _now(),
        "detail": {
            "target_speaker": target,
            "backend": backend_detail,
            "neo4j": {
                "uri": eval_config.neo4j.uri,
                "database": eval_config.neo4j.database,
                "user": eval_config.neo4j.user,
            },
            "config": {
                "base_threshold": float(args.base_threshold),
                "candidate_top_k": "5 (hardcoded in auth/verify.py get_historical_candidates)",
                "generations": max(1, int(args.generations)),
                "adaptive_threshold_enabled": False,
                "require_challenge": False,
                "artifacts_dir": str(eval_config.paths.artifacts_dir),
            },
            "dataset": {
                "n_manifest_clips": len(records),
                "speakers": sorted({record.speaker for record in records}),
                "impostor_speakers": sorted({record.speaker for record in records if record.speaker != target}),
                "n_enrol_clips": len(enrol_clips),
                "n_holdout_clips": len(eval_clips),
            },
            "lineage": lineage,
            "active_head_degradation": degradation,
        },
        "metrics": metrics,
        "trials": trials,
        "warnings": warnings,
        "fix": [],
        "phase8_evidence": bool(not degraded and fired),
        "phase8_evidence_note": (
            "phase8_evidence is true only when the fallback actually fired on ECAPA-TDNN embeddings "
            "against a live Neo4j graph without head degradation"
        ),
    }
    if cache.entries:
        cache.save()
    return _emit(report, args, out_dir, trials)


def _emit(
    report: dict[str, Any],
    args: argparse.Namespace,
    out_dir: Path,
    trials: list[dict[str, Any]] | None = None,
) -> int:
    report_path = out_dir / "fallback_candidate_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str))
    (out_dir / "fallback_candidate_summary.txt").write_text(_summary(report) + "\n")
    if trials is not None:
        with (out_dir / TRIALS_NAME).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(TRIAL_FIELDS), extrasaction="ignore")
            writer.writeheader()
            writer.writerows(trials)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(_summary(report))
    print(f"\nreport : {report_path}")
    if report["status"] == "skipped":
        return 3
    return 0


def _summary(report: dict[str, Any]) -> str:
    lines = [
        "FALLBACK CANDIDATE SEARCH REPORT",
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
            f"  backend  : source={backend.get('source')} model_loaded={backend.get('model_loaded')} "
            f"synthetic={backend.get('synthetic_embeddings')} cache_hits={backend.get('cache_hits')}"
        )
    lineage = (report.get("detail") or {}).get("lineage") or []
    if lineage:
        lines.append(
            f"  lineage  : {len(lineage)} voiceprint versions written "
            f"(graph_saved={sum(1 for entry in lineage if entry.get('graph_saved'))}/{len(lineage)})"
        )
    for key, value in (report.get("metrics") or {}).items():
        lines.append(f"  {key:<30}: {value}")
    for warning in report.get("warnings") or []:
        lines.append(f"  ! {warning}")
    for command in report.get("fix") or []:
        lines.append(f"  -> {command}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
