from pathlib import Path

from fastapi.testclient import TestClient

from voice_agent.config import AppConfig, PathsConfig
from voice_agent.server.app import create_app
from voice_agent.server.relay.broker import RelayBroker, RelayError
from voice_agent.server.relay.store import RelayStore


def test_broker_can_trust_revoke_and_rotate_device_tokens(tmp_path: Path) -> None:
    store = RelayStore(tmp_path / "relay.sqlite")
    broker = RelayBroker(store, lease_ttl_ms=1000)
    registered = broker.register_device("scope-a", "Scope A", "scott", ["mic"], now_ms=1000)

    trusted = broker.set_device_trust("scope-a", trusted=True, now_ms=1100)
    assert trusted["trusted"] is True

    rotated = broker.rotate_device_token("scope-a", now_ms=1200)
    assert rotated["device_token"].startswith("dt_")
    assert rotated["device_token"] != registered["device_token"]

    try:
        broker.attach("tommy", "scope-a", device_token=registered["device_token"], now_ms=1300)
        raise AssertionError("old device token should fail after rotation")
    except RelayError as exc:
        assert exc.status_code == 401

    attached = broker.attach("tommy", "scope-a", device_token=rotated["device_token"], now_ms=1300)
    assert attached["active_device_id"] == "scope-a"

    revoked = broker.revoke_device("scope-a", now_ms=1400, reason="compromised")
    assert revoked["status"] == "revoked"

    try:
        broker.heartbeat("scope-a", "tommy", attached["lease_token"], now_ms=1500, device_token=rotated["device_token"])
        raise AssertionError("revoked device should not heartbeat")
    except RelayError as exc:
        assert exc.status_code == 403


def test_relay_admin_device_api_requires_admin_token_and_rotates(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TOMMY_RELAY_ADMIN_TOKEN", "admin-secret")
    config = AppConfig(paths=PathsConfig(artifacts_dir=str(tmp_path / "runs"), workspace_dir=str(tmp_path / "workspace")))
    with TestClient(create_app(config)) as client:
        reg = client.post("/relay/devices/register", json={"device_id": "scope-a", "capabilities": ["mic"]}).json()

        denied = client.post("/relay/devices/scope-a/trust", json={"trusted": True})
        assert denied.status_code == 401

        trusted = client.post(
            "/relay/devices/scope-a/trust",
            headers={"x-relay-admin-token": "admin-secret"},
            json={"trusted": True},
        )
        assert trusted.status_code == 200
        assert trusted.json()["trusted"] is True

        rotated = client.post(
            "/relay/devices/scope-a/rotate-token",
            headers={"x-relay-admin-token": "admin-secret"},
        )
        assert rotated.status_code == 200
        assert rotated.json()["device_token"] != reg["device_token"]

        revoked = client.post(
            "/relay/devices/scope-a/revoke",
            headers={"x-relay-admin-token": "admin-secret"},
            json={"reason": "lost laptop"},
        )
        assert revoked.status_code == 200
        assert revoked.json()["status"] == "revoked"


def test_revoked_devices_cannot_resume_or_open_webrtc(tmp_path: Path) -> None:
    store = RelayStore(tmp_path / "relay.sqlite")
    broker = RelayBroker(store, lease_ttl_ms=1000)
    registered = broker.register_device("scope-a", "Scope A", "scott", ["mic"], now_ms=1000)
    attached = broker.attach("tommy", "scope-a", device_token=registered["device_token"], now_ms=1100)
    broker.revoke_device("scope-a", now_ms=1200, reason="lost")

    try:
        broker.resume("tommy", "scope-a", attached["resume_token"], device_token=registered["device_token"], now_ms=1300)
        raise AssertionError("revoked device should not resume a durable session")
    except RelayError as exc:
        assert exc.status_code == 403

    try:
        broker.webrtc_offer("tommy", "scope-a", "v=0\r\n", lease_token=attached["lease_token"], device_token=registered["device_token"], now_ms=1300)
        raise AssertionError("revoked device should not open WebRTC negotiation")
    except RelayError as exc:
        assert exc.status_code == 403

    try:
        broker.webrtc_offer("tommy", "unknown-scope", "v=0\r\n", now_ms=1300)
        raise AssertionError("unknown device should not open WebRTC negotiation")
    except RelayError as exc:
        assert exc.status_code == 404


def test_relay_client_dashboard_and_webrtc_signaling_plane(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TOMMY_RELAY_ADMIN_TOKEN", "admin-secret")
    config = AppConfig(paths=PathsConfig(artifacts_dir=str(tmp_path / "runs"), workspace_dir=str(tmp_path / "workspace")))
    with TestClient(create_app(config)) as client:
        tommy = client.get("/tommy")
        assert tommy.status_code == 200
        assert "Tommy Telescope Relay" in tommy.text
        assert "/relay/sessions/tommy/stream" in tommy.text
        assert "MediaRecorder" in tommy.text
        assert "RTCPeerConnection" in tommy.text

        dashboard = client.get("/relay/dashboard", headers={"x-relay-admin-token": "admin-secret"})
        assert dashboard.status_code == 200
        assert "Relay Dashboard" in dashboard.text
        assert "force-handoff" in dashboard.text

        reg = client.post("/relay/devices/register", json={"device_id": "scope-a", "capabilities": ["browser_audio", "mic"]}).json()
        attached = client.post("/relay/sessions/tommy/attach", json={"device_id": "scope-a", "device_token": reg["device_token"]}).json()
        offer = client.post(
            "/relay/sessions/tommy/webrtc/offer",
            json={"device_id": "scope-a", "device_token": reg["device_token"], "lease_token": attached["lease_token"], "sdp": "v=0\r\n", "type": "offer"},
        )
        assert offer.status_code == 200
        body = offer.json()
        assert body["ok"] is True
        assert body["transport"] == "webrtc"
        assert body["status"] == "signaling_ready"
        assert body["signaling"]["offer_id"].startswith("wo_")
        assert body["signaling"]["answer_endpoint"] == f"/relay/sessions/tommy/webrtc/offers/{body['signaling']['offer_id']}/answer"
        assert "ice_servers" in body

        pending = client.get(
            "/relay/sessions/tommy/webrtc/offers/pending",
            headers={"x-relay-admin-token": "admin-secret"},
        )
        assert pending.status_code == 200
        assert pending.json()["offers"][0]["offer_id"] == body["signaling"]["offer_id"]

        answer = client.post(
            f"/relay/sessions/tommy/webrtc/offers/{body['signaling']['offer_id']}/answer",
            json={"device_id": "scope-a", "device_token": reg["device_token"], "lease_token": attached["lease_token"], "sdp": "v=0\r\na=answer\r\n", "type": "answer"},
        )
        assert answer.status_code == 200
        assert answer.json()["status"] == "answered"

        candidate = client.post(
            f"/relay/sessions/tommy/webrtc/offers/{body['signaling']['offer_id']}/candidate",
            json={"device_id": "scope-a", "device_token": reg["device_token"], "lease_token": attached["lease_token"], "candidate": "candidate:1 1 udp 1 127.0.0.1 9 typ host", "sdp_mid": "0", "sdp_mline_index": 0},
        )
        assert candidate.status_code == 200
        assert candidate.json()["status"] == "candidate_recorded"

        negotiation = client.post(
            f"/relay/sessions/tommy/webrtc/offers/{body['signaling']['offer_id']}/status",
            json={"device_id": "scope-a", "device_token": reg["device_token"], "lease_token": attached["lease_token"]},
        )
        assert negotiation.status_code == 200
        negotiation_body = negotiation.json()
        assert negotiation_body["status"] == "answered"
        assert negotiation_body["offer"]["sdp"] == "v=0\r\n"
        assert negotiation_body["answer"]["sdp"] == "v=0\r\na=answer\r\n"
        assert negotiation_body["candidates"][0]["candidate"].startswith("candidate:1")

        pending_after_answer = client.get(
            "/relay/sessions/tommy/webrtc/offers/pending",
            headers={"x-relay-admin-token": "admin-secret"},
        )
        assert pending_after_answer.status_code == 200
        assert pending_after_answer.json()["offers"] == []




def test_mesh_device_metadata_and_status_include_operational_fields(tmp_path: Path) -> None:
    store = RelayStore(tmp_path / "relay.sqlite")
    broker = RelayBroker(store, lease_ttl_ms=1000)
    registered = broker.register_device(
        "scope-a",
        "Scope A",
        "scott",
        ["mic", "speaker", "fallback"],
        platform="linux",
        mesh_node="x1-370",
        now_ms=1000,
        fallback_priority=5,
        audio_source="pipewire:default",
        tailscale_ip="100.64.0.10",
        location="office",
    )
    assert registered["audio_source"] == "pipewire:default"
    assert registered["tailscale_ip"] == "100.64.0.10"
    assert registered["location"] == "office"

    attached = broker.attach("tommy", "scope-a", device_token=registered["device_token"], now_ms=1100)
    status = broker.session_status("tommy", now_ms=1200)
    assert status["active_device"]["mesh_node"] == "x1-370"
    assert status["active_device"]["audio_source"] == "pipewire:default"
    assert status["lease_ttl_ms_remaining"] == attached["lease_expires_at_ms"] - 1200


def test_pending_transcript_outbox_survives_worker_restart(tmp_path: Path) -> None:
    store = RelayStore(tmp_path / "relay.sqlite")
    broker = RelayBroker(store, lease_ttl_ms=1000)
    device = broker.register_device("scope-a", "Scope A", "scott", ["mic"], now_ms=1000)
    attached = broker.attach("tommy", "scope-a", device_token=device["device_token"], now_ms=1000)
    recorded = broker.record_transcript(
        "tommy",
        "scope-a",
        attached["lease_token"],
        seq=1,
        text="hello tommy",
        partial=False,
        now_ms=1010,
        device_token=device["device_token"],
    )
    pending = broker.pending_transcripts(limit=10)
    assert pending["items"][0]["id"] == recorded["transcript_id"]
    assert pending["items"][0]["text"] == "hello tommy"

    broker.complete_transcript(recorded["transcript_id"], "tommy", "scope-a", error="temporary outage", now_ms=1020)
    assert broker.pending_transcripts(limit=10)["items"] == []
    failed = broker.failed_transcripts(limit=10)
    assert failed["items"][0]["id"] == recorded["transcript_id"]
    retried = broker.retry_transcript(recorded["transcript_id"], now_ms=1030)
    assert retried["item"]["error"] is None
    assert broker.pending_transcripts(limit=10)["items"][0]["id"] == recorded["transcript_id"]

    broker.complete_transcript(recorded["transcript_id"], "tommy", "scope-a", response_text="done", now_ms=1040)
    assert broker.pending_transcripts(limit=10)["items"] == []
