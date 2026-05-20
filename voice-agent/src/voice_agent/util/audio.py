from __future__ import annotations

import base64
from pathlib import Path
from typing import Tuple

import importlib.util
import numpy as np

sf_spec = importlib.util.find_spec("soundfile")
if sf_spec is not None:
    import soundfile as sf
else:  # pragma: no cover
    sf = None


def pcm16_bytes_to_float(pcm_bytes: bytes) -> np.ndarray:
    data = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    return data


def float_to_pcm16_bytes(samples: np.ndarray) -> bytes:
    samples = np.clip(samples, -1.0, 1.0)
    return (samples * 32767.0).astype(np.int16).tobytes()


def read_wav(path: str) -> Tuple[np.ndarray, int]:
    if sf is not None:
        data, sr = sf.read(path, dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        return data, sr
    import wave

    with wave.open(path, "rb") as wf:
        sr = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        return audio, sr


def write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if sf is not None:
        sf.write(path, samples, sample_rate, subtype="PCM_16")
        return
    import wave

    pcm = float_to_pcm16_bytes(samples)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


def b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("utf-8")


def b64decode(data: str) -> bytes:
    return base64.b64decode(data.encode("utf-8"))
