from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from voice_agent.config import AppConfig, PathsConfig, ServerConfig
from voice_agent.server.app import create_app


def test_health_status_intent_and_chat(tmp_path: Path) -> None:
    config = AppConfig(paths=PathsConfig(artifacts_dir=str(tmp_path / "runs"), workspace_dir=str(tmp_path / "workspace")))
    client = TestClient(create_app(config))

    assert client.get("/healthz").json()["ok"] is True
    status = client.get("/status").json()
    assert status["service"] == "sophia-voice-agent"
    assert status["protocol"] == "native_ws"

    intent = client.post("/intent", json={"transcript": "please write this down"}).json()
    assert intent["intent"] == "dictation"
    assert "hermes_prompt" in intent

    chat = client.post("/voice-chat", json={"transcript": "what is the status", "session_id": "dash"}).json()
    assert chat["intent"] == "question"
    assert chat["response"]

    events = client.get("/events", params={"session_id": "dash"}).json()["events"]
    assert {event["type"] for event in events} >= {"intent_detected", "llm_output"}


def test_hermes_overlay_websocket_ack(tmp_path: Path) -> None:
    config = AppConfig(
        paths=PathsConfig(artifacts_dir=str(tmp_path / "runs"), workspace_dir=str(tmp_path / "workspace")),
        server=ServerConfig(protocol="hermes_overlay_v1"),
    )
    client = TestClient(create_app(config))

    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "protocol": "hermes_overlay_v1",
                "frame": {
                    "type": "start_session",
                    "payload": {
                        "session_id": "overlay",
                        "sample_rate": 16000,
                        "channels": 1,
                        "encoding": "pcm_s16le",
                        "user_id": "default",
                    },
                },
                "meta": {},
            }
        )
        ack = ws.receive_json()
        assert ack["protocol"] == "hermes_overlay_v1"
        assert ack["frame"]["type"] == "ack"
        assert ack["frame"]["payload"]["received"] == "start_session"
        event = ws.receive_json()
        assert event["frame"]["type"] == "event"
        assert event["frame"]["payload"]["type"] == "session_start"
