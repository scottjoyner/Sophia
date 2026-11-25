#!/usr/bin/env python3
"""Build a tiny synthetic cross-mic corpus (manifest + embeddings cache).

This is a **self-test fixture**, not an accuracy measurement: the embeddings are
random speaker/device vectors, not ECAPA-TDNN output. It exists so the
cross-mic harness can be exercised end-to-end (and in CI) with no audio files, no
model download and no labelled corpus in the repo.

    python scripts/make_synthetic_cross_mic_manifest.py --out-dir ../reports/synthetic
    python scripts/eval_cross_mic_accuracy.py \
        --manifest ../reports/synthetic/manifest.jsonl \
        --out-dir ../reports/synthetic_run \
        --embeddings-cache ../reports/synthetic/embeddings.json

Corpus shape: the target speaker is enrolled on two devices, a third device is
never enrolled (the cross-mic case), and two further devices carry impostor
speakers -- one of them a *close* speaker, so target/impostor scores overlap.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from voice_agent.auth.verification_eval import (  # noqa: E402
    load_manifest,
    synthetic_embedding_cache,
)

# device -> (impostor speaker or None, (n_enroll, n_calib, n_eval), channel offset, clip spread)
DEVICES: dict[str, tuple[str | None, tuple[int, int, int], float, float]] = {
    "phone": (None, (3, 2, 4), 0.05, 0.04),
    "laptop": (None, (3, 2, 4), 0.15, 0.22),
    "car-mic": (None, (0, 2, 3), 0.35, 0.10),
    "dashcam-2025": ("dashcam_copilot", (0, 2, 3), 0.12, 0.08),
    "kitchen-speaker": ("kitchen_visitor", (0, 2, 3), 0.10, 0.08),
}
TARGET_SPEAKER = "scott"
# Impostors whose embeddings sit close to the target: this is what makes FAR move.
SIMILAR_SPEAKERS = {"dashcam_copilot": (TARGET_SPEAKER, 0.70)}


def build(out_dir: Path, dim: int, seed: int) -> tuple[Path, Path]:
    rng = random.Random(seed)
    rows: list[dict[str, str]] = []
    counter = 0
    for device, (impostor, splits, _penalty, _spread) in DEVICES.items():
        for split, count in zip(("enroll", "calib", "eval"), splits, strict=True):
            for _ in range(count):
                counter += 1
                rows.append(
                    {
                        "clip_id": f"{device}-{split}-{counter:03d}",
                        "path": f"/nonexistent/{device}/{split}/{counter:03d}.wav",
                        "speaker": impostor or TARGET_SPEAKER,
                        "device_id": device,
                        "split": split,
                        "sha256": f"{rng.randrange(1 << 256):064x}",
                    }
                )
    rng.shuffle(rows)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = out_dir / "manifest.jsonl"
    manifest.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    cache = synthetic_embedding_cache(
        load_manifest(manifest),
        out_dir / "embeddings.json",
        dim=dim,
        seed=seed,
        device_penalties={device: values[2] for device, values in DEVICES.items()},
        device_spreads={device: values[3] for device, values in DEVICES.items()},
        similar_speakers=SIMILAR_SPEAKERS,
    )
    cache.save()
    return manifest, cache.path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a synthetic cross-mic eval corpus.")
    parser.add_argument("--out-dir", required=True, help="Where to write manifest.jsonl + embeddings.json.")
    parser.add_argument("--dim", type=int, default=192, help="Embedding dimension (192 = ECAPA-TDNN).")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    manifest, cache = build(Path(args.out_dir), args.dim, args.seed)
    print(f"manifest: {manifest}\ncache   : {cache}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
