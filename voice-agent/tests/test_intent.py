from __future__ import annotations

from voice_agent.llm.intent import (
    build_hermes_prompt,
    detect_voice_intent,
    intent_from_model_payload,
)


def test_detect_command() -> None:
    result = detect_voice_intent("open the garage door")
    assert result.name == "command"
    assert result.confidence == 0.72


def test_detect_question() -> None:
    result = detect_voice_intent("what time is the meeting?")
    assert result.name == "question"
    assert result.confidence == 0.7


def test_detect_dictation() -> None:
    result = detect_voice_intent("note this down for later")
    assert result.name == "dictation"
    assert result.confidence == 0.78


def test_detect_empty_is_chat() -> None:
    result = detect_voice_intent("   ")
    assert result.name == "chat"
    assert result.confidence == 0.0


def test_detect_plain_statement_is_chat() -> None:
    result = detect_voice_intent("the weather is nice today")
    assert result.name == "chat"
    assert result.confidence == 0.55


def test_intent_from_model_payload_valid() -> None:
    result = intent_from_model_payload("remind me", {"intent": "command", "confidence": 0.99})
    assert result.name == "command"
    assert result.confidence == 0.99
    assert result.source == "draft_model"


def test_intent_from_model_payload_invalid_name_falls_back() -> None:
    result = intent_from_model_payload("hello", {"intent": "bogus", "confidence": "nope"})
    assert result.name == "chat"
    assert 0.0 <= result.confidence <= 1.0


def test_build_hermes_prompt_contains_transcript() -> None:
    prompt = build_hermes_prompt("call mom", "command", 0.8)
    assert "call mom" in prompt
    assert "command" in prompt
