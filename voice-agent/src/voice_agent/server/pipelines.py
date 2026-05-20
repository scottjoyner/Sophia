from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from ..auth.verify import verify_audio_segment
from ..config import AppConfig
from ..llm.ralph_loop import RalphLoop
from ..stt.faster_whisper_batch import refine_transcript
from ..stt.faster_whisper_stream import StreamingTranscriber
from ..tts.base import TextToSpeech
from ..tts.piper_tts import PiperTTS
from ..tts.pyttsx3_fallback import FallbackTTS
from ..util.audio import write_wav
from ..util.db import Database
from ..util.logging import JsonlLogger
from ..util.time import now_ms


class PipelineManager:
    def __init__(self, config: AppConfig, base_dir: Path):
        self.config = config
        self.base_dir = base_dir
        self.logger = JsonlLogger(self.base_dir / "events.jsonl")
        self.db = Database(self.base_dir / "results.sqlite")
        self.transcriber = StreamingTranscriber(config.stt)
        self.ralph = RalphLoop(config)
        self.tts = self._build_tts()

    def _build_tts(self) -> TextToSpeech:
        if self.config.tts.backend == "piper":
            try:
                return PiperTTS(self.config)
            except Exception:
                return FallbackTTS(self.config)
        return FallbackTTS(self.config)

    def handle_streaming(self, state, samples: np.ndarray, t_client_ms: int) -> None:
        partial = self.transcriber.process(samples, t_client_ms)
        if partial:
            payload = {
                "session_id": state.session_id,
                "ts_ms": now_ms(),
                "text": partial,
            }
            self.logger.log(
                "stt_partial",
                payload,
            )
            self.db.log_event(state.session_id, "stt_partial", payload)

    def handle_segment(self, state, samples: np.ndarray, segment_path: Path) -> None:
        final_text = self.transcriber.finalize(samples)
        final_payload = {"session_id": state.session_id, "ts_ms": now_ms(), "text": final_text}
        self.logger.log("stt_final", final_payload)
        self.db.log_event(state.session_id, "stt_final", final_payload)
        refined_text = None
        if self.config.stt.refine_enabled:
            refined_text = refine_transcript(samples, state.sample_rate)
            refine_payload = {"session_id": state.session_id, "ts_ms": now_ms(), "text": refined_text}
            self.logger.log("stt_refine", refine_payload)
            self.db.log_event(state.session_id, "stt_refine", refine_payload)
        auth = verify_audio_segment(self.config, state.session_id, state.user_id, samples, state.sample_rate)
        auth_payload = {
            "session_id": state.session_id,
            "ts_ms": now_ms(),
            "user_id": auth.get("user_id"),
            "score": auth.get("score"),
            "accepted": auth.get("accepted"),
            "challenge": auth.get("challenge"),
        }
        self.logger.log("auth_decision", auth_payload)
        self.db.log_event(state.session_id, "auth_decision", auth_payload)
        if not auth.get("accepted"):
            return
        answer = self.ralph.run(final_text)
        llm_payload = {"session_id": state.session_id, "ts_ms": now_ms(), "text": answer}
        self.logger.log("llm_output", llm_payload)
        self.db.log_event(state.session_id, "llm_output", llm_payload)
        audio = self.tts.synthesize(answer)
        out_path = self.base_dir / "tts" / f"{state.session_id}_{now_ms()}.wav"
        write_wav(out_path, audio, self.tts.sample_rate)
        tts_payload = {"session_id": state.session_id, "ts_ms": now_ms(), "path": str(out_path)}
        self.logger.log("tts_output", tts_payload)
        self.db.log_event(state.session_id, "tts_output", tts_payload)

    def finish_session(self, state) -> None:
        self.transcriber.reset()
