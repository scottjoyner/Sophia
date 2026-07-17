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


def test_relay_client_and_dashboard_pages_and_webrtc_skeleton(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TOMMY_RELAY_ADMIN_TOKEN", "admin-secret")
    config = AppConfig(paths=PathsConfig(artifacts_dir=str(tmp_path / "runs"), workspace_dir=str(tmp_path / "workspace")))
    with TestClient(create_app(config)) as client:
        tommy = client.get("/tommy")
        assert tommy.status_code == 200
        assert "Tommy Telescope Relay" in tommy.text
        assert "/relay/sessions/tommy/stream" in tommy.text

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
        assert body["status"] == "not_configured"
        assert "ice_servers" in body
