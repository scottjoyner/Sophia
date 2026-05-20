from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import uvicorn

from voice_agent.auth.enroll import enroll_from_files
from voice_agent.bench.replay_client import replay_wav
from voice_agent.config import AppConfig, AuthConfig, PathsConfig, ServerConfig
from voice_agent.server.app import create_app


def write_sample_wav(path: Path, seconds: float = 1.0, sample_rate: int = 16000) -> None:
    import math
    import wave

    frames = int(seconds * sample_rate)
    samples = [
        int(0.1 * 32767 * math.sin(2 * math.pi * 440 * t / sample_rate)) for t in range(frames)
    ]
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"".join(int(s).to_bytes(2, byteorder="little", signed=True) for s in samples))


def run_server(app, host: str, port: int, ready: threading.Event) -> None:
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)

    def startup():
        ready.set()

    server.install_signal_handlers = lambda: None
    app.add_event_handler("startup", startup)
    server.run()


def test_end_to_end_replay(tmp_path: Path) -> None:
    artifacts = tmp_path / "runs"
    wav_path = tmp_path / "sample.wav"
    write_sample_wav(wav_path)
    config = AppConfig(
        auth=AuthConfig(threshold=0.1, require_challenge=False),
        paths=PathsConfig(artifacts_dir=str(artifacts), workspace_dir=str(tmp_path / "workspace")),
        server=ServerConfig(host="127.0.0.1", port=9876),
    )
    enroll_from_files(config, "default", [str(wav_path)])

    app = create_app(config)
    ready = threading.Event()
    thread = threading.Thread(target=run_server, args=(app, "127.0.0.1", 9876, ready), daemon=True)
    thread.start()
    ready.wait(timeout=5)
    time.sleep(0.5)

    asyncio.run(replay_wav("ws://127.0.0.1:9876/ws", str(wav_path), "default", session_id="test"))
    time.sleep(1.0)

    events_path = artifacts / "events.jsonl"
    assert events_path.exists()
    lines = events_path.read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    types = {event["type"] for event in events}
    assert "stt_partial" in types
    assert "stt_final" in types
    assert "auth_decision" in types
    assert "llm_output" in types
    assert "tts_output" in types

    tts_entries = [event for event in events if event["type"] == "tts_output"]
    tts_path = Path(tts_entries[0]["payload"]["path"])
    assert tts_path.exists()

    sqlite_path = artifacts / "results.sqlite"
    assert sqlite_path.exists()
    import sqlite3

    conn = sqlite3.connect(sqlite_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM events")
    count = cur.fetchone()[0]
    assert count > 0
