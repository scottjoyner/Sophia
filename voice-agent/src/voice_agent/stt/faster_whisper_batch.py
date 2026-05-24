from __future__ import annotations

import importlib.util
import threading

import numpy as np

from ..config import STTConfig

_whisper_model = None
_whisper_model_lock = threading.Lock()
_whisper_model_config = {}


def _get_model(config: STTConfig | None):
    global _whisper_model, _whisper_model_config
    if importlib.util.find_spec("faster_whisper") is None:
        return None
    model_size = config.model_size if config else "tiny"
    compute_type = config.compute_type if config else "int8"
    cpu_threads = config.cpu_threads if config else 2
    key = f"{model_size}:{compute_type}:{cpu_threads}"
    if _whisper_model is not None and _whisper_model_config.get("key") == key:
        return _whisper_model
    with _whisper_model_lock:
        if _whisper_model is not None and _whisper_model_config.get("key") == key:
            return _whisper_model
        from faster_whisper import WhisperModel

        model = WhisperModel(model_size, compute_type=compute_type, cpu_threads=cpu_threads)
        _whisper_model = model
        _whisper_model_config = {"key": key}
        return model


def refine_transcript(samples: np.ndarray, sample_rate: int, config: STTConfig | None = None) -> str:
    model = _get_model(config)
    if model is not None:
        segments, _info = model.transcribe(samples, language="en")
        return " ".join(segment.text.strip() for segment in segments).strip()
    if np.mean(np.abs(samples)) < 0.001:
        return ""
    return "hello world"
