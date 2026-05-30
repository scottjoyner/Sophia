from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from fastapi import HTTPException, UploadFile


@dataclass(frozen=True)
class UploadPolicy:
    name: str
    max_bytes: int
    allowed_suffixes: frozenset[str]

    @classmethod
    def build(cls, name: str, max_bytes: int, allowed_suffixes: Iterable[str]) -> "UploadPolicy":
        return cls(
            name=name,
            max_bytes=max_bytes,
            allowed_suffixes=frozenset(s.lower() if s.startswith(".") else f".{s.lower()}" for s in allowed_suffixes),
        )


VERIFY_AUDIO_POLICY = UploadPolicy.build(
    "verify audio",
    15 * 1024 * 1024,
    [".wav", ".webm", ".m4a", ".mp3", ".ogg", ".opus"],
)

CAPTURE_AUDIO_POLICY = UploadPolicy.build(
    "capture audio",
    50 * 1024 * 1024,
    [".wav", ".webm", ".m4a", ".mp3", ".ogg", ".opus", ".txt"],
)

VOICEPRINT_AUDIO_POLICY = UploadPolicy.build(
    "voiceprint audio",
    50 * 1024 * 1024,
    [".wav", ".webm", ".m4a", ".mp3", ".ogg", ".opus"],
)

MEETING_AUDIO_POLICY = UploadPolicy.build(
    "meeting audio",
    500 * 1024 * 1024,
    [".wav", ".webm", ".m4a", ".mp3", ".ogg", ".opus", ".mp4", ".mov", ".mkv", ".avi"],
)


def safe_upload_suffix(upload: UploadFile, *, default: str = ".wav", policy: UploadPolicy | None = None) -> str:
    suffix = default
    if upload.filename and "." in upload.filename:
        candidate = "." + upload.filename.rsplit(".", 1)[-1].lower()
        if 1 < len(candidate) <= 12:
            suffix = candidate
    if policy and suffix.lower() not in policy.allowed_suffixes:
        allowed = ", ".join(sorted(policy.allowed_suffixes))
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported {policy.name} file type '{suffix}'. Allowed: {allowed}",
        )
    return suffix


async def read_upload_with_limits(upload: UploadFile, policy: UploadPolicy) -> bytes:
    suffix = safe_upload_suffix(upload, default=".webm", policy=policy)
    del suffix  # validation side effect only
    data = await upload.read()
    if not data:
        raise HTTPException(status_code=400, detail=f"Empty {policy.name} upload")
    if len(data) > policy.max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"{policy.name.capitalize()} upload is too large: {len(data)} bytes > {policy.max_bytes} bytes",
        )
    return data


async def save_upload_with_limits(upload: UploadFile, dest_dir: Path, prefix: str, policy: UploadPolicy, *, default_suffix: str = ".webm") -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = safe_upload_suffix(upload, default=default_suffix, policy=policy)
    data = await read_upload_with_limits(upload, policy)
    import uuid

    path = dest_dir / f"{prefix}_{uuid.uuid4().hex}{suffix}"
    path.write_bytes(data)
    return path
