from pathlib import Path

from fastapi.testclient import TestClient

from voice_agent.config import AppConfig, PathsConfig
from voice_agent.server.app import create_app
from voice_agent.server.relay.broker import RelayBroker, RelayError
from voice_agent.server.relay.store import RelayStore


def test_broker_auth_attach_handoff_resume_and_expiry(tmp_path: Path) -> None:
    store = RelayStore(tmp_path / "relay.sqlite")
    broker = RelayBroker(store, lease_ttl_ms=1000)

    a = broker.register_device(
        device_id="scope-a",
        name="Scope A",
        owner_id="scott",
        capabilities=["mic", "speaker"],
        platform="linux",
        mesh_node="x1-370",
        now_ms=1000,
        fallback_priority=50,
    )
    b = broker.register_device(
        device_id="scope-b",
        name="Scope B",
        owner_id="scott",
        capabilities=["mic"],
        platform="browser",
        mesh_node="laptop",
        now_ms=1100,
        fallback_priority=10,
    )

    try:
        broker.attach("tommy", "scope-a", owner_id="scott", now_ms=1200)
        raise AssertionError("attach without device token should fail")
    except RelayError as exc:
        assert exc.status_code == 401

    attached = broker.attach("tommy", "scope-a", owner_id="scott", now_ms=1200, device_token=a["device_token"])
    assert attached["active_device_id"] == "scope-a"
    assert attached["lease_expires_at_ms"] == 2200
    assert attached["lease_token"].startswith("lt_")
    assert attached["resume_token"].startswith("rt_")

    renewed = broker.heartbeat(
        device_id="scope-a",
        session_id="tommy",
        lease_token=attached["lease_token"],
        seq=7,
        now_ms=1500,
        device_token=a["device_token"],
    )
    assert renewed["active"] is True
    assert renewed["lease_expires_at_ms"] == 2500
    assert broker.session_status("tommy", now_ms=1600)["active_device_id"] == "scope-a"

    moved = broker.handoff(
        "tommy",
        from_device_id="scope-a",
        to_device_id="scope-b",
        reason="manual",
        now_ms=1700,
        lease_token=attached["lease_token"],
        device_token=b["device_token"],
    )
    assert moved["active_device_id"] == "scope-b"
    assert moved["lease_token"] != attached["lease_token"]

    resumed = broker.resume("tommy", "scope-a", moved["resume_token"], now_ms=1800, device_token=a["device_token"])
    assert resumed["active_device_id"] == "scope-a"
    assert "replay" in resumed

    try:
        broker.handoff("tommy", "scope-a", "scope-b", lease_token=attached["lease_token"], device_token=b["device_token"], now_ms=3000)
        raise AssertionError("expired lease token should not authorize handoff")
    except RelayError as exc:
        assert exc.status_code == 409

    broker.set_device_trust("scope-b", trusted=True, now_ms=1900)
    expired = broker.session_status("tommy", now_ms=3000)
    assert expired["active_device_id"] == "scope-b"
    assert expired["reason"] == "fallback_promotion"


def test_broker_deduplicates_audio_sequence_and_tracks_gaps(tmp_path: Path) -> None:
    store = RelayStore(tmp_path / "relay.sqlite")
    broker = RelayBroker(store, lease_ttl_ms=1000)
    device = broker.register_device("scope-a", "Scope A", "scott", ["mic"], now_ms=1000)
    attached = broker.attach("tommy", "scope-a", owner_id="scott", now_ms=1000, device_token=device["device_token"])

    try:
        broker.detach("tommy", "scope-a", device_token=device["device_token"], now_ms=1005)
        raise AssertionError("detach without current lease token should fail")
    except RelayError as exc:
        assert exc.status_code == 409

    try:
        broker.record_audio_chunk(
            "tommy", "scope-a", attached["lease_token"], seq=1, encoding="pcm_s16le", byte_count=128, now_ms=1006
        )
        raise AssertionError("audio without device token should fail")
    except RelayError as exc:
        assert exc.status_code == 401

    try:
        broker.record_transcript("tommy", "scope-a", attached["lease_token"], seq=1, text="hello", now_ms=1007)
        raise AssertionError("transcript without device token should fail")
    except RelayError as exc:
        assert exc.status_code == 401

    first = broker.record_audio_chunk(
        "tommy", "scope-a", attached["lease_token"], seq=1, encoding="pcm_s16le", byte_count=128, now_ms=1010, device_token=device["device_token"]
    )
    duplicate = broker.record_audio_chunk(
        "tommy", "scope-a", attached["lease_token"], seq=1, encoding="pcm_s16le", byte_count=128, now_ms=1020, device_token=device["device_token"]
    )
    second = broker.record_audio_chunk(
        "tommy", "scope-a", attached["lease_token"], seq=3, encoding="pcm_s16le", byte_count=64, now_ms=1030, device_token=device["device_token"]
    )

    assert first["accepted"] is True
    assert first["gap"] == [0, 0]
    assert duplicate["accepted"] is False
    assert duplicate["reason"] == "duplicate_sequence"
    assert second["accepted"] is True
    status = broker.session_status("tommy", now_ms=1040)
    assert status["last_seq"] == 3
    assert [2, 2] in status["missing_ranges"]

    filled = broker.record_audio_chunk(
        "tommy", "scope-a", attached["lease_token"], seq=2, encoding="pcm_s16le", byte_count=64, now_ms=1050, device_token=device["device_token"]
    )
    assert filled["accepted"] is True
    filled_status = broker.session_status("tommy", now_ms=1060)
    assert filled_status["last_seq"] == 3
    assert filled_status["missing_ranges"] == [[0, 0]]


def test_detach_requires_active_current_lease(tmp_path: Path) -> None:
    store = RelayStore(tmp_path / "relay.sqlite")
    broker = RelayBroker(store, lease_ttl_ms=100)
    device = broker.register_device("scope-a", "Scope A", "scott", ["mic"], now_ms=1000)
    attached = broker.attach("tommy", "scope-a", owner_id="scott", now_ms=1000, device_token=device["device_token"])

    try:
        broker.detach("tommy", "scope-a", attached["lease_token"], now_ms=1200, device_token=device["device_token"])
        raise AssertionError("expired lease token should not detach")
    except RelayError as exc:
        assert exc.status_code == 409

    fresh = broker.resume("tommy", "scope-a", attached["resume_token"], now_ms=1210, device_token=device["device_token"])
    detached = broker.detach("tommy", "scope-a", fresh["lease_token"], now_ms=1220, device_token=device["device_token"])
    assert detached["state"] == "parked"

    try:
        broker.detach("tommy", "scope-a", fresh["lease_token"], now_ms=1230, device_token=device["device_token"])
        raise AssertionError("revoked lease token should not detach again")
    except RelayError as exc:
        assert exc.status_code == 409


def test_relay_http_api_mounts_on_app_and_persists_events(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TOMMY_RELAY_ADMIN_TOKEN", "admin-secret")
    admin_headers = {"x-relay-admin-token": "admin-secret"}
    config = AppConfig(paths=PathsConfig(artifacts_dir=str(tmp_path / "runs"), workspace_dir=str(tmp_path / "workspace")))
    with TestClient(create_app(config)) as client:
        register = client.post(
            "/relay/devices/register",
            json={
                "device_id": "scope-a",
                "name": "Scope A",
                "owner_id": "scott",
                "capabilities": ["mic", "speaker"],
                "platform": "linux",
                "mesh_node": "x1-370",
            },
        )
        assert register.status_code == 200
        reg_body = register.json()
        assert reg_body["ok"] is True
        assert "device_token" in reg_body

        attach = client.post(
            "/relay/sessions/tommy/attach",
            json={"device_id": "scope-a", "owner_id": "scott", "device_token": reg_body["device_token"]},
        )
        assert attach.status_code == 200
        body = attach.json()
        assert body["ok"] is True
        assert body["active_device_id"] == "scope-a"

        hb = client.post(
            "/relay/devices/heartbeat",
            json={
                "device_id": "scope-a",
                "device_token": reg_body["device_token"],
                "session_id": "tommy",
                "lease_token": body["lease_token"],
                "seq": 3,
            },
        )
        assert hb.status_code == 200
        assert hb.json()["active"] is True

        missing_audio_token = client.post(
            "/relay/sessions/tommy/audio",
            json={"device_id": "scope-a", "lease_token": body["lease_token"], "seq": 4, "byte_count": 16},
        )
        assert missing_audio_token.status_code == 401

        missing_transcript_token = client.post(
            "/relay/sessions/tommy/transcript",
            json={"device_id": "scope-a", "lease_token": body["lease_token"], "seq": 5, "text": "hello"},
        )
        assert missing_transcript_token.status_code == 401

        audio = client.post(
            "/relay/sessions/tommy/audio",
            json={
                "device_id": "scope-a",
                "device_token": reg_body["device_token"],
                "lease_token": body["lease_token"],
                "seq": 4,
                "byte_count": 16,
            },
        )
        assert audio.status_code == 200
        assert audio.json()["accepted"] is True

        transcript = client.post(
            "/relay/sessions/tommy/transcript",
            json={
                "device_id": "scope-a",
                "device_token": reg_body["device_token"],
                "lease_token": body["lease_token"],
                "seq": 5,
                "text": "hello",
            },
        )
        assert transcript.status_code == 200
        assert transcript.json()["accepted"] is True

        status = client.get("/relay/sessions/tommy", headers=admin_headers)
        assert status.status_code == 200
        assert status.json()["active_device_id"] == "scope-a"

        health = client.get("/relay/health", headers=admin_headers)
        assert health.status_code == 200
        assert health.json()["worker"]["started"] is True

        events = client.get("/relay/events", headers=admin_headers)
        event_types = [event["type"] for event in events.json()["events"]]
        assert "relay_device_registered" in event_types
        assert "relay_session_attached" in event_types
