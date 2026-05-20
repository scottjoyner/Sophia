from __future__ import annotations

from typing import Protocol

import numpy as np


class TextToSpeech(Protocol):
    sample_rate: int

    def synthesize(self, text: str) -> np.ndarray: ...
