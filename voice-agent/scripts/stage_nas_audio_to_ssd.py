#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Iterable


AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".webm"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi"}
DEFAULT_EXCLUDE_PARTS = {"dashcam"}


def _iter_files(root: Path, *, include_video: bool, exclude_parts: set[str]) -> Iterable[Path]:
    allowed = set(AUDIO_EXTENSIONS)
    if include_video:
        allowed |= VIDEO_EXTENSIONS
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        parts = {part.lower() for part in path.relative_to(root).parts[:-1]}
        if parts & exclude_parts:
            continue
        if path.suffix.lower() in allowed:
            yield path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_file(src: Path, dst: Path, *, checksum: bool) -> bool:
    if not dst.exists():
        return False
    src_stat = src.stat()
    dst_stat = dst.stat()
    if src_stat.st_size != dst_stat.st_size:
        return False
    if checksum:
        return _sha256(src) == _sha256(dst)
    return int(src_stat.st_mtime) <= int(dst_stat.st_mtime)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage NAS audio onto local SSD for Sophia/Hermes ingest.")
    parser.add_argument("--source", default="/media/scott/NAS1/fileserver/audio")
    parser.add_argument("--dest", default="/mnt/S/sophia-ingest/audio")
    parser.add_argument("--manifest", default="/mnt/S/sophia-ingest/manifests/staged-audio.jsonl")
    parser.add_argument("--container-prefix", default="/ssd-ingest")
    parser.add_argument("--exclude-part", action="append", default=sorted(DEFAULT_EXCLUDE_PARTS))
    parser.add_argument("--include-video", action="store_true", help="Include video files. Default keeps dashcam/video on NAS.")
    parser.add_argument("--checksum", action="store_true", help="Hash matching files before skipping. Slower but stricter.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--progress-every", type=int, default=500)
    args = parser.parse_args()

    source = Path(args.source).resolve()
    dest = Path(args.dest).resolve()
    manifest = Path(args.manifest).resolve()
    if not source.exists():
        print(f"source not found: {source}", file=sys.stderr)
        return 2
    if not args.dry_run:
        dest.mkdir(parents=True, exist_ok=True)
        manifest.parent.mkdir(parents=True, exist_ok=True)

    copied = skipped = planned = errors = 0
    started = time.time()
    exclude_parts = {part.lower() for part in args.exclude_part}
    manifest_handle = None if args.dry_run else manifest.open("a", encoding="utf-8")
    try:
        for src in _iter_files(source, include_video=args.include_video, exclude_parts=exclude_parts):
            rel = src.relative_to(source)
            dst = dest / rel
            if args.limit and planned >= args.limit:
                break
            planned += 1
            try:
                if _same_file(src, dst, checksum=args.checksum):
                    skipped += 1
                    continue
                if not args.quiet:
                    print(f"{'would copy' if args.dry_run else 'copy'} {src} -> {dst}")
                if not args.dry_run:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    record = {
                        "source_path": str(src),
                        "staged_path": str(dst),
                        "container_path": str(Path(args.container_prefix) / dst.relative_to(dest.parent)),
                        "relative_path": str(rel),
                        "size": dst.stat().st_size,
                        "mtime": int(dst.stat().st_mtime),
                        "extension": dst.suffix.lower(),
                        "sha256": _sha256(dst) if args.checksum else "",
                    }
                    manifest_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    manifest_handle.flush()
                    os.fsync(manifest_handle.fileno())
                copied += 1
                if args.quiet and args.progress_every and copied % args.progress_every == 0:
                    print(json.dumps({"copied": copied, "skipped": skipped, "planned": planned, "last": str(dst)}))
            except Exception as exc:  # keep long migrations moving
                errors += 1
                print(f"error: {src}: {type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        if manifest_handle:
            manifest_handle.close()
    elapsed = time.time() - started
    print(
        json.dumps(
            {
                "source": str(source),
                "dest": str(dest),
                "manifest": str(manifest),
                "planned": planned,
                "copied": copied,
                "skipped": skipped,
                "errors": errors,
                "elapsed_seconds": round(elapsed, 3),
                "dry_run": args.dry_run,
            },
            indent=2,
        )
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
