from __future__ import annotations

import importlib.util
import numpy as np

from ..config import AppConfig


class PiperTTS:
    def __init__(self, config: AppConfig):
        self.sample_rate = 16000
        self.voice = config.tts.voice
        self._piper = None
        if importlib.util.find_spec("piper") is None:
            raise RuntimeError("piper not available")
        import piper

        self._piper = piper

    def synthesize(self, text: str) -> np.ndarray:
        if self._piper is None:
            return np.zeros(int(self.sample_rate * 0.5), dtype=np.float32)
        # Placeholder for actual piper usage
        return np.zeros(int(self.sample_rate * 0.5), dtype=np.float32)
