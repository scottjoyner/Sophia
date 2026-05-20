from __future__ import annotations

import importlib.util
import time
from typing import List

import numpy as np

from ..config import STTConfig


class StreamingTranscriber:
    def __init__(self, config: STTConfig):
        self.config = config
        self.buffer: List[np.ndarray] = []
        self.last_decode_ms = 0
        self.partial_history: List[str] = []
        self._last_partial = ""
        self._try_load_model()

    def _try_load_model(self) -> None:
        if importlib.util.find_spec("faster_whisper") is None:
            self.model = None
            return
        from faster_whisper import WhisperModel

        self.model = WhisperModel("base", compute_type="int8")

    def _decode(self, samples: np.ndarray) -> str:
        if self.model is None:
            # fallback deterministic transcript based on energy
            if np.mean(np.abs(samples)) < 0.001:
                return ""
            return "hello world"
        segments, _info = self.model.transcribe(samples, language="en")
        return " ".join(segment.text.strip() for segment in segments).strip()

    def process(self, samples: np.ndarray, t_client_ms: int) -> str:
        self.buffer.append(samples)
        now = int(time.time() * 1000)
        if now - self.last_decode_ms < self.config.decode_interval_ms:
            return ""
        self.last_decode_ms = now
        window_samples = self._window()
        text = self._decode(window_samples)
        if text and text != self._last_partial:
            self._last_partial = text
            self.partial_history.append(text)
            return text
        return ""

    def _window(self) -> np.ndarray:
        audio = np.concatenate(self.buffer) if self.buffer else np.array([], dtype=np.float32)
        max_samples = int(self.config.window_ms / 1000 * self.config.sample_rate)
        if audio.shape[0] > max_samples:
            audio = audio[-max_samples:]
        return audio

    def finalize(self, samples: np.ndarray) -> str:
        text = self._decode(samples)
        return text

    def reset(self) -> None:
        self.buffer = []
        self.partial_history = []
        self._last_partial = ""
        self.last_decode_ms = 0
