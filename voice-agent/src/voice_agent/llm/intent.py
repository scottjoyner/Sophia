from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional


VoiceIntentType = Literal["dictation", "command", "question", "chat"]


@dataclass
class VoiceIntent:
    name: VoiceIntentType
    confidence: float
    transcript: str
    hermes_prompt: Optional[str] = None
    voice_intent: str = ""
    source: str = "heuristic"


_COMMAND_RE = re.compile(
    r"\b(open|run|start|stop|search|find|create|write|send|schedule|remind|summarize|deploy|install|check)\b",
    re.IGNORECASE,
)
_DICTATION_RE = re.compile(r"\b(note|dictate|write this down|transcribe|journal)\b", re.IGNORECASE)
_QUESTION_RE = re.compile(r"\?\s*$|\b(what|why|how|when|where|who|can you|could you)\b", re.IGNORECASE)


def detect_voice_intent(transcript: str) -> VoiceIntent:
    text = " ".join(transcript.strip().split())
    if not text:
        return VoiceIntent("chat", 0.0, text)
    if _DICTATION_RE.search(text):
        name: VoiceIntentType = "dictation"
        confidence = 0.78
    elif _COMMAND_RE.search(text):
        name = "command"
        confidence = 0.72
    elif _QUESTION_RE.search(text):
        name = "question"
        confidence = 0.7
    else:
        name = "chat"
        confidence = 0.55
    return VoiceIntent(name, confidence, text, build_hermes_prompt(text, name, confidence))


def intent_from_model_payload(transcript: str, payload: dict) -> VoiceIntent:
    fallback = detect_voice_intent(transcript)
    raw_name = str(payload.get("intent") or fallback.name).lower()
    name: VoiceIntentType = raw_name if raw_name in {"dictation", "command", "question", "chat"} else fallback.name
    try:
        confidence = float(payload.get("confidence", fallback.confidence))
    except (TypeError, ValueError):
        confidence = fallback.confidence
    confidence = max(0.0, min(1.0, confidence))
    normalized = " ".join(str(payload.get("transcript") or transcript).strip().split())
    raw = payload.get("hermes_prompt")
    prompt = str(raw) if raw else build_hermes_prompt(normalized, name, confidence)
    return VoiceIntent(name, confidence, normalized, prompt, source="draft_model")


def build_hermes_prompt(transcript: str, intent: VoiceIntentType, confidence: float) -> str:
    return (
        f"Voice input detected. Intent: {intent} (confidence {confidence:.2f}).\n"
        "Treat speech recognition errors as possible and ask a concise clarification if the action is ambiguous.\n"
        "If this is dictation, preserve the user's wording and clean only obvious transcription artifacts.\n"
        "If this is a command, execute only after the requested target and action are clear.\n\n"
        f"Transcript:\n{transcript}"
    )
