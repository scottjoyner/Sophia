from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np
import uvicorn
import pytest

from voice_agent.auth.enroll import enroll_from_files
from voice_agent.bench.replay_client import replay_wav
from voice_agent.config import AppConfig, AuthConfig, PathsConfig, ServerConfig
from voice_agent.server.app import create_app
from voice_agent import server as server_pkg


def write_sample_wav(path: Path, seconds: float = 2.5, sample_rate: int = 16000) -> None:
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


def run_server(app, host: str, port: int) -> None:
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    server.run()


def wait_for_healthz(url: str, timeout_s: float = 10.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise TimeoutError(f"Timed out waiting for {url}")


def wait_for_event_types(events_path: Path, expected: set[str], timeout_s: float = 15.0) -> set[str]:
    deadline = time.time() + timeout_s
    seen: set[str] = set()
    while time.time() < deadline:
        if events_path.exists():
            lines = events_path.read_text(encoding="utf-8").splitlines()
            events = [json.loads(line) for line in lines if line.strip()]
            seen = {event["type"] for event in events}
            if expected.issubset(seen):
                return seen
        time.sleep(0.5)
    return seen


def test_end_to_end_replay(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakeRalph:
        def __init__(self, config):  # noqa: ARG002
            pass

        def run(self, prompt: str) -> str:  # noqa: ARG002
            return "Synthesized response"

    class FakeTTS:
        sample_rate = 16000

        def __init__(self, config):  # noqa: ARG002
            pass

        def synthesize(self, text: str):  # noqa: ARG002
            return np.zeros(16000, dtype=np.float32)

    monkeypatch.setattr(server_pkg.pipelines, "RalphLoop", FakeRalph)
    monkeypatch.setattr(server_pkg.pipelines, "OpenVoiceTTS", FakeTTS)
    monkeypatch.setattr(server_pkg.pipelines, "PiperTTS", FakeTTS)
    monkeypatch.setattr(server_pkg.pipelines, "CoquiTTS", FakeTTS)
    monkeypatch.setattr(server_pkg.pipelines, "FallbackTTS", FakeTTS)
    monkeypatch.setattr(
        server_pkg.pipelines,
        "verify_audio_segment",
        lambda *args, **kwargs: {
            "user_id": "default",
            "score": 0.95,
            "accepted": True,
            "challenge": None,
        },
    )

    artifacts = tmp_path / "runs"
    wav_path = tmp_path / "sample.wav"
    write_sample_wav(wav_path, seconds=2.5)
    config = AppConfig(
        auth=AuthConfig(threshold=0.1, require_challenge=False),
        paths=PathsConfig(artifacts_dir=str(artifacts), workspace_dir=str(tmp_path / "workspace")),
        server=ServerConfig(host="127.0.0.1", port=9876),
    )
    enroll_from_files(config, "default", [str(wav_path)])

    app = create_app(config)
    thread = threading.Thread(target=run_server, args=(app, "127.0.0.1", 9876), daemon=True)
    thread.start()
    wait_for_healthz("http://127.0.0.1:9876/healthz")

    asyncio.run(replay_wav("ws://127.0.0.1:9876/ws", str(wav_path), "default", session_id="test"))

    events_path = artifacts / "events.jsonl"
    types = wait_for_event_types(
        events_path,
        {"stt_partial", "stt_final", "auth_decision", "llm_output", "tts_output"},
    )
    assert "stt_partial" in types
    assert "stt_final" in types
    assert "auth_decision" in types
    assert "llm_output" in types
    assert "tts_output" in types

    lines = events_path.read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
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
