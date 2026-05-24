from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np

from ..auth.verify import verify_audio_segment
from ..config import AppConfig
from ..llm.ralph_loop import RalphLoop
from ..llm.intent import detect_voice_intent
from ..stt.faster_whisper_batch import refine_transcript
from ..stt.faster_whisper_stream import StreamingTranscriber
from ..tts.base import TextToSpeech
from ..tts.piper_tts import PiperTTS
from ..tts.pyttsx3_fallback import FallbackTTS
from ..tts.openvoice_tts import OpenVoiceTTS
from ..tts.coqui_tts import CoquiTTS
from ..util.audio import write_wav
from ..util.db import Database
from ..util.logging import JsonlLogger
from ..util.time import now_ms


EventCallback = Callable[[str, Dict[str, object]], None]


class PipelineManager:
    def __init__(self, config: AppConfig, base_dir: Path, event_callback: Optional[EventCallback] = None):
        self.config = config
        self.base_dir = base_dir
        self._event_callback = event_callback
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
        if self.config.tts.backend == "openvoice":
            try:
                return OpenVoiceTTS(self.config)
            except Exception:
                return FallbackTTS(self.config)
        if self.config.tts.backend == "coqui":
            try:
                return CoquiTTS(self.config)
            except Exception:
                return FallbackTTS(self.config)
        return FallbackTTS(self.config)

    def _emit(self, event_type: str, payload: Dict[str, object]) -> None:
        if self._event_callback:
            self._event_callback(event_type, payload)

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
            self._emit("stt_partial", payload)

    def handle_segment(self, state, samples: np.ndarray, segment_path: Path) -> None:
        final_text = self.transcriber.finalize(samples)
        final_payload = {"session_id": state.session_id, "ts_ms": now_ms(), "text": final_text}
        self.logger.log("stt_final", final_payload)
        self.db.log_event(state.session_id, "stt_final", final_payload)
        self._emit("stt_final", final_payload)
        refined_text = None
        if self.config.stt.refine_enabled:
            refined_text = refine_transcript(samples, state.sample_rate, self.config.stt)
            refine_payload = {"session_id": state.session_id, "ts_ms": now_ms(), "text": refined_text}
            self.logger.log("stt_refine", refine_payload)
            self.db.log_event(state.session_id, "stt_refine", refine_payload)
            self._emit("stt_refine", refine_payload)
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
        self._emit("auth_decision", auth_payload)
        if not auth.get("accepted"):
            return
        intent = detect_voice_intent(refined_text or final_text)
        intent_payload = {
            "session_id": state.session_id,
            "ts_ms": now_ms(),
            "intent": intent.name,
            "confidence": intent.confidence,
            "transcript": intent.transcript,
            "hermes_prompt": intent.hermes_prompt,
        }
        self.logger.log("intent_detected", intent_payload)
        self.db.log_event(state.session_id, "intent_detected", intent_payload)
        self._emit("intent_detected", intent_payload)
        answer = self.ralph.run(intent.hermes_prompt)
        llm_payload = {"session_id": state.session_id, "ts_ms": now_ms(), "text": answer}
        self.logger.log("llm_output", llm_payload)
        self.db.log_event(state.session_id, "llm_output", llm_payload)
        self._emit("llm_output", llm_payload)
        audio = self.tts.synthesize(answer)
        out_path = self.base_dir / "tts" / f"{state.session_id}_{now_ms()}.wav"
        write_wav(out_path, audio, self.tts.sample_rate)
        tts_payload = {"session_id": state.session_id, "ts_ms": now_ms(), "path": str(out_path)}
        self.logger.log("tts_output", tts_payload)
        self.db.log_event(state.session_id, "tts_output", tts_payload)
        self._emit("tts_output", tts_payload)

    def finish_session(self, state) -> None:
        self.transcriber.reset()
