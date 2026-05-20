from __future__ import annotations

import numpy as np

from ..config import AppConfig


class FallbackTTS:
    def __init__(self, config: AppConfig):
        self.sample_rate = 16000
        self.voice = config.tts.voice

    def synthesize(self, text: str) -> np.ndarray:
        # Offline placeholder: generate short tone
        duration_s = max(0.4, min(2.0, len(text) / 50))
        t = np.linspace(0, duration_s, int(self.sample_rate * duration_s), False)
        tone = 0.1 * np.sin(2 * np.pi * 440 * t)
        return tone.astype(np.float32)
