from pathlib import Path

from fastapi.testclient import TestClient

from voice_agent.config import AppConfig, PathsConfig
from voice_agent.server.app import create_app
from voice_agent.server.relay.broker import RelayBroker, RelayError
from voice_agent.server.relay.store import RelayStore


def test_broker_requires_tokens_for_handoff_resume_heartbeat_and_webrtc(tmp_path: Path) -> None:
    store = RelayStore(tmp_path / "relay.sqlite")
    broker = RelayBroker(store, lease_ttl_ms=1000)
    a = broker.register_device("scope-a", "Scope A", "scott", ["mic"], now_ms=1000)
    b = broker.register_device("scope-b", "Scope B", "scott", ["mic"], now_ms=1000)
    attached = broker.attach("tommy", "scope-a", device_token=a["device_token"], now_ms=1100)

    for action in (
        lambda: broker.heartbeat("scope-a", "tommy", attached["lease_token"], now_ms=1200),
        lambda: broker.resume("tommy", "scope-a", attached["resume_token"], now_ms=1200),
        lambda: broker.handoff("tommy", "scope-a", "scope-b", lease_token=attached["lease_token"], now_ms=1200),
        lambda: broker.webrtc_offer("tommy", "scope-a", "v=0\r\n", lease_token=attached["lease_token"], now_ms=1200),
        lambda: broker.record_audio_chunk("tommy", "scope-a", attached["lease_token"], seq=1, encoding="pcm_s16le", byte_count=1, now_ms=1200),
        lambda: broker.record_transcript("tommy", "scope-a", attached["lease_token"], seq=1, text="hi", now_ms=1200),
        lambda: broker.detach("tommy", "scope-a", attached["lease_token"], now_ms=1200),
    ):
        try:
            action()
            raise AssertionError("operation without device token should fail closed")
        except RelayError as exc:
            assert exc.status_code == 401

    try:
        broker.handoff("tommy", "scope-a", "scope-b", device_token=b["device_token"], now_ms=1200)
        raise AssertionError("handoff without current lease token should fail")
    except RelayError as exc:
        assert exc.status_code == 409

    resumed = broker.resume("tommy", "scope-a", attached["resume_token"], device_token=a["device_token"], now_ms=1200)
    assert resumed["active_device_id"] == "scope-a"
    rtc = broker.webrtc_offer("tommy", "scope-a", "v=0\r\n", lease_token=resumed["lease_token"], device_token=a["device_token"], now_ms=1210)
    assert rtc["fallback"]["endpoint"] == "/relay/sessions/tommy/stream"


def test_tokenless_legacy_devices_fail_closed(tmp_path: Path) -> None:
    store = RelayStore(tmp_path / "relay.sqlite")
    broker = RelayBroker(store, lease_ttl_ms=1000)
    store.upsert_device(
        {
            "device_id": "legacy-scope",
            "name": "Legacy Scope",
            "owner_id": "scott",
            "capabilities": ["mic"],
            "status": "online",
            "last_seen_ms": 1000,
            "token_hash": None,
            "trusted": 1,
            "fallback_priority": 1,
        }
    )

    try:
        broker.attach("tommy", "legacy-scope", now_ms=1100)
        raise AssertionError("tokenless legacy device should not attach")
    except RelayError as exc:
        assert exc.status_code == 401

    refreshed = broker.register_device("legacy-scope", "Legacy Scope", "scott", ["mic"], now_ms=1200)
    assert refreshed["device_token"].startswith("dt_")
    attached = broker.attach("tommy", "legacy-scope", device_token=refreshed["device_token"], now_ms=1300)
    assert attached["active_device_id"] == "legacy-scope"


def test_public_relay_responses_do_not_expose_secret_hashes(tmp_path: Path) -> None:
    store = RelayStore(tmp_path / "relay.sqlite")
    broker = RelayBroker(store, lease_ttl_ms=1000)
    device = broker.register_device("scope-a", "Scope A", "scott", ["mic"], now_ms=1000)
    attached = broker.attach("tommy", "scope-a", device_token=device["device_token"], now_ms=1100)

    bodies = [
        broker.list_devices(),
        broker.list_sessions(),
        broker.session_status("tommy", now_ms=1200),
        broker.timeline("tommy"),
    ]
    serialized = repr(bodies)
    assert "token_hash" not in serialized
    assert "resume_token_hash" not in serialized
    assert "lease_token_hash" not in serialized
    assert attached["resume_token"] not in serialized
    assert attached["lease_token"] not in serialized


def test_public_registration_cannot_self_trust_or_enter_fallback_pool(tmp_path: Path) -> None:
    store = RelayStore(tmp_path / "relay.sqlite")
    broker = RelayBroker(store, lease_ttl_ms=1000)
    public = broker.register_device("attacker-scope", "Attacker", "scott", ["mic"], fallback_priority=1, trusted=True, now_ms=1000)
    assert public["trusted"] is False
    assert public["fallback_priority"] >= 1000
    assert store.candidate_fallback_devices(owner_id="scott", now_ms=1000) == []

    broker.set_device_trust("attacker-scope", trusted=True, now_ms=1100)
    trusted_candidates = store.candidate_fallback_devices(owner_id="scott", now_ms=1100)
    assert [device["device_id"] for device in trusted_candidates] == ["attacker-scope"]

    try:
        broker.register_device("attacker-scope", "Spoof", "scott", ["mic"], fallback_priority=1, trusted=True, now_ms=1200)
        raise AssertionError("existing device refresh without device token should fail closed")
    except RelayError as exc:
        assert exc.status_code == 401

    refreshed = broker.register_device("attacker-scope", "Real", "scott", ["mic"], device_token=public["device_token"], fallback_priority=1, trusted=True, now_ms=1300)
    assert refreshed["trusted"] is True


def test_force_handoff_without_target_token_requires_trusted_device(tmp_path: Path) -> None:
    store = RelayStore(tmp_path / "relay.sqlite")
    broker = RelayBroker(store, lease_ttl_ms=1000)
    a = broker.register_device("scope-a", "Scope A", "scott", ["mic"], now_ms=1000)
    b = broker.register_device("scope-b", "Scope B", "scott", ["mic"], now_ms=1000)
    broker.attach("tommy", "scope-a", device_token=a["device_token"], now_ms=1100)

    try:
        broker.handoff("tommy", "scope-a", "scope-b", force=True, now_ms=1200)
        raise AssertionError("force handoff without target token should reject untrusted target")
    except RelayError as exc:
        assert exc.status_code == 403

    moved_with_token = broker.handoff("tommy", "scope-a", "scope-b", force=True, device_token=b["device_token"], now_ms=1210)
    assert moved_with_token["active_device_id"] == "scope-b"

    resumed = broker.resume("tommy", "scope-a", moved_with_token["resume_token"], device_token=a["device_token"], now_ms=1220)
    broker.set_device_trust("scope-b", trusted=True, now_ms=1230)
    moved_trusted = broker.handoff("tommy", "scope-a", "scope-b", force=True, lease_token=resumed["lease_token"], now_ms=1240)
    assert moved_trusted["active_device_id"] == "scope-b"


def test_relay_admin_boundaries_fail_closed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TOMMY_RELAY_ADMIN_TOKEN", "admin-secret")
    config = AppConfig(paths=PathsConfig(artifacts_dir=str(tmp_path / "runs"), workspace_dir=str(tmp_path / "workspace")))
    with TestClient(create_app(config)) as client:
        reg = client.post("/relay/devices/register", json={"device_id": "scope-a", "capabilities": ["mic"]}).json()
        attached = client.post("/relay/sessions/tommy/attach", json={"device_id": "scope-a", "device_token": reg["device_token"]}).json()

        for method, path in (
            ("get", "/relay/devices"),
            ("get", "/relay/sessions"),
            ("get", "/relay/sessions/tommy"),
            ("get", "/relay/sessions/tommy/timeline"),
            ("get", "/relay/sessions/tommy/gaps"),
            ("get", "/relay/sessions/tommy/webrtc/offers/pending"),
            ("get", "/relay/events"),
            ("get", "/relay/live-events"),
            ("get", "/relay/health"),
            ("get", "/relay/readiness"),
            ("get", "/relay/dashboard"),
        ):
            response = getattr(client, method)(path)
            assert response.status_code == 401, path

        for path in ("/relay/sessions/tommy/expire", "/relay/sessions/tommy/force-handoff"):
            response = client.post(path, json={"to_device_id": "scope-a"})
            assert response.status_code == 401, path

        ok = client.get("/relay/sessions/tommy", headers={"x-relay-admin-token": "admin-secret"})
        assert ok.status_code == 200
        assert "token_hash" not in repr(ok.json())

        public_status = client.get("/status")
        assert public_status.status_code == 200
        assert "relay" not in public_status.json()

        denied_force = client.post(
            "/relay/sessions/tommy/attach",
            json={"device_id": "scope-a", "device_token": reg["device_token"], "force": True},
        )
        assert denied_force.status_code == 401

        denied_webrtc = client.post(
            "/relay/sessions/tommy/webrtc/offer",
            json={"device_id": "scope-a", "device_token": reg["device_token"], "lease_token": attached["lease_token"], "sdp": "v=0\r\n"},
        )
        assert denied_webrtc.status_code == 200
        offer_id = denied_webrtc.json()["signaling"]["offer_id"]

        denied_negotiation_status = client.post(
            f"/relay/sessions/tommy/webrtc/offers/{offer_id}/status",
            json={"device_id": "scope-a"},
        )
        assert denied_negotiation_status.status_code == 401


def test_websocket_requires_auth_before_event_access(tmp_path: Path) -> None:
    config = AppConfig(paths=PathsConfig(artifacts_dir=str(tmp_path / "runs"), workspace_dir=str(tmp_path / "workspace")))
    with TestClient(create_app(config)) as client:
        with client.websocket_connect("/relay/sessions/tommy/stream") as ws:
            ws.send_json({"type": "events"})
            body = ws.receive_json()
            assert body["type"] == "error"
            assert body["status_code"] == 401

        reg = client.post("/relay/devices/register", json={"device_id": "scope-a", "capabilities": ["mic"]}).json()
        attached = client.post("/relay/sessions/tommy/attach", json={"device_id": "scope-a", "device_token": reg["device_token"]}).json()
        other = client.post("/relay/devices/register", json={"device_id": "scope-b", "capabilities": ["mic"]}).json()
        with client.websocket_connect("/relay/sessions/tommy/stream") as ws:
            ws.send_json({"type": "authenticate", "device_id": "scope-b", "device_token": other["device_token"]})
            body = ws.receive_json()
            assert body["type"] == "error"
            assert body["status_code"] == 401

        with client.websocket_connect("/relay/sessions/tommy/stream") as ws:
            ws.send_json({"type": "authenticate", "device_id": "scope-a", "device_token": reg["device_token"], "lease_token": attached["lease_token"]})
            assert ws.receive_json()["type"] == "authenticated"
            ws.send_json({"type": "events"})
            received = {"type": "missing"}
            for _ in range(5):
                received = ws.receive_json()
                if received["type"] == "events":
                    break
            assert received["type"] == "events"
