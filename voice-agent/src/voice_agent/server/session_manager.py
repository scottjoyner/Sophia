from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from ..config import AppConfig
from ..util.audio import pcm16_bytes_to_float, write_wav
from ..util.db import Database
from ..util.logging import JsonlLogger
from ..util.time import now_ms
from ..util.vad import VadSegment, create_vad
from .pipelines import PipelineManager


@dataclass
class SessionState:
    session_id: str
    sample_rate: int
    channels: int
    encoding: str
    user_id: str = "default"
    buffer: List[np.ndarray] = field(default_factory=list)
    segments: List[np.ndarray] = field(default_factory=list)
    segment_start_ms: Optional[int] = None
    last_partial_text: str = ""


class SessionManager:
    def __init__(self, config: AppConfig, base_dir: Path):
        self.config = config
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.sessions: Dict[str, SessionState] = {}
        self.logger = JsonlLogger(self.base_dir / "events.jsonl")
        self.db = Database(self.base_dir / "results.sqlite")
        self.pipeline = PipelineManager(config, self.base_dir)

    def start_session(self, session_id: str, sample_rate: int, channels: int, encoding: str, user_id: str) -> None:
        vad = create_vad(
            sample_rate,
            self.config.vad.aggressiveness,
            self.config.vad.energy_threshold,
            self.config.vad.min_speech_ms,
            self.config.vad.max_silence_ms,
        )
        state = SessionState(
            session_id=session_id,
            sample_rate=sample_rate,
            channels=channels,
            encoding=encoding,
            user_id=user_id,
        )
        state.vad = vad
        self.sessions[session_id] = state
        payload = {"session_id": session_id, "ts_ms": now_ms()}
        self.logger.log("session_start", payload)
        self.db.log_event(session_id, "session_start", payload)

    def handle_audio_chunk(self, session_id: str, pcm_bytes: bytes, chunk_ms: int, t_client_ms: int) -> None:
        state = self.sessions[session_id]
        samples = pcm16_bytes_to_float(pcm_bytes)
        state.buffer.append(samples)
        segments = state.vad.process(samples, chunk_ms)
        self.pipeline.handle_streaming(state, samples, t_client_ms)
        for segment in segments:
            self._finalize_segment(state, segment)

    def end_session(self, session_id: str) -> None:
        state = self.sessions[session_id]
        segments = state.vad.flush()
        for segment in segments:
            self._finalize_segment(state, segment)
        payload = {"session_id": session_id, "ts_ms": now_ms()}
        self.logger.log("session_end", payload)
        self.db.log_event(session_id, "session_end", payload)
        self.pipeline.finish_session(state)
        self.sessions.pop(session_id, None)

    def _finalize_segment(self, state: SessionState, segment: VadSegment) -> None:
        samples = np.concatenate(state.buffer) if state.buffer else np.array([], dtype=np.float32)
        start = int(segment.start_ms / 1000 * state.sample_rate)
        end = int(segment.end_ms / 1000 * state.sample_rate)
        segment_samples = samples[start:end]
        segment_path = self.base_dir / "segments" / f"{state.session_id}_{segment.start_ms}_{segment.end_ms}.wav"
        write_wav(segment_path, segment_samples, state.sample_rate)
        self.pipeline.handle_segment(state, segment_samples, segment_path)
