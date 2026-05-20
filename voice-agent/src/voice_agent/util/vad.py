from __future__ import annotations

from dataclasses import dataclass
from typing import List

import importlib.util
import numpy as np


@dataclass
class VadSegment:
    start_ms: int
    end_ms: int


class EnergyVad:
    def __init__(self, sample_rate: int, energy_threshold: float, min_speech_ms: int, max_silence_ms: int):
        self.sample_rate = sample_rate
        self.energy_threshold = energy_threshold
        self.min_speech_ms = min_speech_ms
        self.max_silence_ms = max_silence_ms
        self.reset()

    def reset(self) -> None:
        self.in_speech = False
        self.speech_start_ms = 0
        self.last_voice_ms = 0
        self.cursor_ms = 0

    def process(self, samples: np.ndarray, chunk_ms: int) -> List[VadSegment]:
        energy = float(np.mean(np.abs(samples)))
        segments: List[VadSegment] = []
        if energy >= self.energy_threshold:
            if not self.in_speech:
                self.in_speech = True
                self.speech_start_ms = self.cursor_ms
            self.last_voice_ms = self.cursor_ms
        else:
            if self.in_speech and (self.cursor_ms - self.last_voice_ms) >= self.max_silence_ms:
                end_ms = self.last_voice_ms
                if end_ms - self.speech_start_ms >= self.min_speech_ms:
                    segments.append(VadSegment(self.speech_start_ms, end_ms))
                self.in_speech = False
        self.cursor_ms += chunk_ms
        return segments

    def flush(self) -> List[VadSegment]:
        segments: List[VadSegment] = []
        if self.in_speech:
            end_ms = self.last_voice_ms
            if end_ms - self.speech_start_ms >= self.min_speech_ms:
                segments.append(VadSegment(self.speech_start_ms, end_ms))
        self.reset()
        return segments


def create_vad(sample_rate: int, aggressiveness: int, energy_threshold: float, min_speech_ms: int, max_silence_ms: int):
    if importlib.util.find_spec("webrtcvad") is not None:
        return WebrtcVad(sample_rate, aggressiveness, min_speech_ms, max_silence_ms)
    return EnergyVad(sample_rate, energy_threshold, min_speech_ms, max_silence_ms)


class WebrtcVad:
    def __init__(self, sample_rate: int, aggressiveness: int, min_speech_ms: int, max_silence_ms: int):
        import webrtcvad

        self.sample_rate = sample_rate
        self.vad = webrtcvad.Vad(aggressiveness)
        self.min_speech_ms = min_speech_ms
        self.max_silence_ms = max_silence_ms
        self.reset()

    def reset(self) -> None:
        self.in_speech = False
        self.speech_start_ms = 0
        self.last_voice_ms = 0
        self.cursor_ms = 0

    def process(self, samples: np.ndarray, chunk_ms: int) -> List[VadSegment]:
        pcm = (samples * 32767.0).astype(np.int16).tobytes()
        is_speech = self.vad.is_speech(pcm, self.sample_rate)
        segments: List[VadSegment] = []
        if is_speech:
            if not self.in_speech:
                self.in_speech = True
                self.speech_start_ms = self.cursor_ms
            self.last_voice_ms = self.cursor_ms
        else:
            if self.in_speech and (self.cursor_ms - self.last_voice_ms) >= self.max_silence_ms:
                end_ms = self.last_voice_ms
                if end_ms - self.speech_start_ms >= self.min_speech_ms:
                    segments.append(VadSegment(self.speech_start_ms, end_ms))
                self.in_speech = False
        self.cursor_ms += chunk_ms
        return segments

    def flush(self) -> List[VadSegment]:
        segments: List[VadSegment] = []
        if self.in_speech:
            end_ms = self.last_voice_ms
            if end_ms - self.speech_start_ms >= self.min_speech_ms:
                segments.append(VadSegment(self.speech_start_ms, end_ms))
        self.reset()
        return segments
