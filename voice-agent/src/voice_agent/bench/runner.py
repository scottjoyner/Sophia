from __future__ import annotations

import asyncio
from pathlib import Path

from .datasets import load_manifest
from .replay_client import replay_wav


def run_bench(dataset_path: str, out_dir: str, url: str) -> None:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(dataset_path)
    for item in manifest:
        session_id = item.get("id", "session")
        asyncio.run(replay_wav(url, item["audio_path"], item.get("user_id", "default"), session_id=session_id))
