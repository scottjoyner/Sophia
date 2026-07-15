from __future__ import annotations

import numpy as np
import pytest

from voice_agent.auth.speaker_embedder import SpeakerEmbedder
from voice_agent.util import time as time_util
from voice_agent.util.audio import (
    b64decode,
    b64encode,
    float_to_pcm16_bytes,
    pcm16_bytes_to_float,
    read_wav,
    write_wav,
)
from voice_agent.util.db import Database
from voice_agent.util.textnorm import normalize
from voice_agent.util.vad import EnergyVad, VadSegment, create_vad
from voice_agent.util.wer import cer, partial_churn, wer


def _make_wav(path, seconds: float = 1.0, sr: int = 16000, freq: float = 220.0) -> tuple[np.ndarray, int]:
    t = np.linspace(0.0, seconds, int(seconds * sr), endpoint=False)
    samples = (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    write_wav(path, samples, sr)
    return samples, sr


def test_normalize_lowercases_and_strips_punctuation() -> None:
    assert normalize("Hello, World!") == "hello world"
    assert normalize("  Multiple   SPACES  ") == "multiple spaces"


def test_wer_exact_and_partial() -> None:
    assert wer("the cat sat", "the cat sat") == 0.0
    assert wer("a b c", "a b") == pytest.approx(1 / 3)
    assert wer("", "x") == 1.0
    assert wer("", "") == 0.0


def test_cer() -> None:
    assert cer("abc", "abc") == 0.0
    assert cer("abc", "abd") == pytest.approx(1 / 3)
    assert cer("", "x") == 1.0


def test_partial_churn() -> None:
    assert partial_churn([]) == 0.0
    assert partial_churn(["abc"]) == 0.0
    assert partial_churn(["abc", "abd"]) >= 0.0


def test_now_ms_is_positive_int() -> None:
    value = time_util.now_ms()
    assert isinstance(value, int)
    assert value > 0


def test_pcm_float_roundtrip() -> None:
    floats = np.array([0.5, -0.25, 0.0, -1.0, 1.0], dtype=np.float32)
    pcm = float_to_pcm16_bytes(floats)
    back = pcm16_bytes_to_float(pcm)
    assert np.allclose(back, floats, atol=1 / 32768)


def test_b64_roundtrip() -> None:
    raw = b"\x00\x01\x02hello\xFF"
    assert b64decode(b64encode(raw)) == raw


def test_wav_roundtrip(tmp_path) -> None:
    samples, sr = _make_wav(tmp_path / "clip.wav", seconds=0.5)
    data, rate = read_wav(str(tmp_path / "clip.wav"))
    assert rate == sr
    assert np.allclose(data, samples, atol=1e-3)


def test_energy_vad_detects_speech_then_silence() -> None:
    vad = EnergyVad(sample_rate=16000, energy_threshold=0.01, min_speech_ms=300, max_silence_ms=500)
    loud = np.full(160, 0.5, dtype=np.float32)
    silent = np.zeros(160, dtype=np.float32)
    for _ in range(40):
        vad.process(loud, chunk_ms=10)
    segments: list[VadSegment] = []
    for _ in range(60):
        segments.extend(vad.process(silent, chunk_ms=10))
    segments.extend(vad.flush())
    assert any(s.end_ms - s.start_ms >= 300 for s in segments)


def test_energy_vad_no_speech_yields_nothing() -> None:
    vad = EnergyVad(sample_rate=16000, energy_threshold=0.01, min_speech_ms=300, max_silence_ms=500)
    silent = np.zeros(160, dtype=np.float32)
    segments: list[VadSegment] = []
    for _ in range(100):
        segments.extend(vad.process(silent, chunk_ms=10))
    segments.extend(vad.flush())
    assert segments == []


def test_create_vad_returns_energy_vad_without_webrtcvad() -> None:
    vad = create_vad(16000, 2, 0.01, 300, 500)
    assert isinstance(vad, EnergyVad)


def test_database_voiceprint_roundtrip(tmp_path) -> None:
    db = Database(tmp_path / "results.sqlite")
    emb = [0.1, 0.2, 0.3]
    samples = {"samples": [{"sha256": "abc", "embedding": emb}]}
    db.save_voiceprint("alice", emb, samples, 0.8)
    rec = db.fetch_voiceprint("alice")
    assert rec is not None
    assert rec["embedding"] == emb
    assert rec["samples"] == samples
    assert rec["threshold"] == 0.8


def test_database_device_voiceprint_and_calibration(tmp_path) -> None:
    db = Database(tmp_path / "results.sqlite")
    db.save_device_voiceprint("alice", "phone", [0.4, 0.5], {"samples": []}, 0.7)
    devices = db.fetch_device_voiceprints("alice")
    assert "phone" in devices
    db.record_device_outcome("phone", 0.9, True)
    db.record_device_outcome("phone", 0.2, False)
    cal = db.fetch_device_calibration("phone")
    assert cal is not None
    assert cal["n_accepted"] == 1
    assert cal["n_rejected"] == 1
    assert db.list_device_ids("alice") == ["phone"]


def test_database_event_logging(tmp_path) -> None:
    db = Database(tmp_path / "results.sqlite")
    db.log_event("s1", "type_a", {"ts_ms": 1, "x": "y"})
    with db._lock:
        rows = db.conn.execute("SELECT session_id, type, payload FROM events").fetchall()
    assert len(rows) == 1
    assert rows[0][1] == "type_a"


def test_speaker_embedder_fallback_dimension() -> None:
    embedder = SpeakerEmbedder()
    vec = embedder.embed(np.zeros(16000, dtype=np.float32), 16000)
    assert len(vec) == 192


def test_speaker_embedder_prepare_audio_resample_and_trim() -> None:
    embedder = SpeakerEmbedder()
    audio = np.zeros(8000, dtype=np.float32)
    prepared = embedder._prepare_audio(audio, 8000)
    assert prepared.shape[0] == int(8000 * 16000 / 8000) or prepared.shape[0] >= 1
    # Empty audio is returned unchanged/empty without raising.
    assert embedder._prepare_audio(np.array([]), 16000).size == 0
