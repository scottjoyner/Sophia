from __future__ import annotations

import json
from typing import Any

from voice_agent import relay_agent


def test_cli_admin_device_commands_send_admin_header(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, Any], dict[str, str] | None]] = []

    def fake_post(base_url: str, path: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
        calls.append((base_url, path, payload, headers))
        return {"ok": True, "path": path}

    monkeypatch.setattr(relay_agent, "_post", fake_post)

    assert relay_agent.main(["--device-id", "scope-a", "--admin-token", "secret", "trust", "--trusted"]) == 0
    assert calls[-1] == (
        "http://127.0.0.1:8765",
        "/relay/devices/scope-a/trust",
        {"trusted": True},
        {"x-relay-admin-token": "secret"},
    )

    assert relay_agent.main(["--device-id", "scope-a", "--admin-token", "secret", "rotate-token"]) == 0
    assert calls[-1][1] == "/relay/devices/scope-a/rotate-token"
    assert calls[-1][2] == {}
    assert calls[-1][3] == {"x-relay-admin-token": "secret"}

    refreshed = relay_agent.main(["--device-id", "scope-a", "--admin-token", "secret", "revoke", "--reason", "lost"])
    assert refreshed == 0
    assert calls[-1] == (
        "http://127.0.0.1:8765",
        "/relay/devices/scope-a/revoke",
        {"reason": "lost"},
        {"x-relay-admin-token": "secret"},
    )


def test_cli_uses_env_tokens_when_not_passed(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, str, dict[str, Any], dict[str, str] | None]] = []

    def fake_post(base_url: str, path: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
        calls.append((base_url, path, payload, headers))
        return {"ok": True}

    monkeypatch.setattr(relay_agent, "_post", fake_post)
    monkeypatch.setenv("TOMMY_RELAY_DEVICE_TOKEN", "device-secret")
    monkeypatch.setenv("TOMMY_RELAY_ADMIN_TOKEN", "admin-secret")

    assert relay_agent.main(["--device-id", "scope-a", "attach"]) == 0
    assert calls[-1][2]["device_token"] == "device-secret"

    assert relay_agent.main(["--device-id", "scope-a", "rotate-token"]) == 0
    assert calls[-1][3] == {"x-relay-admin-token": "admin-secret"}

    assert relay_agent.main(["--device-id", "scope-a", "transcript", "--lease-token", "lease-secret", "--seq", "7", "--text", "hello"]) == 0
    assert calls[-1][1] == "/relay/sessions/tommy/transcript"
    assert calls[-1][2]["device_token"] == "device-secret"

    audio_file = tmp_path / "sample.raw"
    audio_file.write_bytes(b"abc")
    assert relay_agent.main(["--device-id", "scope-a", "audio-file", "--lease-token", "lease-secret", "--seq", "8", "--audio-file", str(audio_file)]) == 0
    assert calls[-1][1] == "/relay/sessions/tommy/audio"
    assert calls[-1][2]["device_token"] == "device-secret"
    assert calls[-1][2]["byte_count"] == 3




def test_cli_enroll_writes_config_and_status_reads_it(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, str, dict[str, Any], dict[str, str] | None]] = []
    config_path = tmp_path / "relay-agent.json"

    def fake_post(base_url: str, path: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
        calls.append((base_url, path, payload, headers))
        if path == "/relay/devices/register":
            return {"ok": True, "device_id": payload["device_id"], "device_token": "dt-issued", "trusted": True, "capabilities": payload["capabilities"]}
        return {"ok": True, "path": path, "active": True, "lease_expires_at_ms": 1234}

    monkeypatch.setattr(relay_agent, "_post", fake_post)

    assert relay_agent.main([
        "--config", str(config_path),
        "--relay", "https://relay.example",
        "--device-id", "scope-a",
        "--session-id", "tommy",
        "enroll",
        "--enrollment-token", "enroll-secret",
        "--capability", "mic",
        "--capability", "speaker",
    ]) == 0

    saved = json.loads(config_path.read_text())
    assert saved["relay_url"] == "https://relay.example"
    assert saved["device_id"] == "scope-a"
    assert saved["device_token"] == "dt-issued"
    assert saved["session_id"] == "tommy"
    assert saved["capabilities"] == ["mic", "speaker"]

    assert relay_agent.main(["--config", str(config_path), "status", "--lease-token", "lt-current"]) == 0
    assert calls[-1][0] == "https://relay.example"
    assert calls[-1][1] == "/relay/devices/heartbeat"
    assert calls[-1][2]["device_id"] == "scope-a"
    assert calls[-1][2]["device_token"] == "dt-issued"


def test_cli_run_once_attaches_then_heartbeats_and_renders_user_service(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, str, dict[str, Any], dict[str, str] | None]] = []
    config_path = tmp_path / "relay-agent.json"
    config_path.write_text(json.dumps({"relay_url": "http://relay", "device_id": "scope-a", "device_token": "dt-token", "session_id": "tommy"}))

    def fake_post(base_url: str, path: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
        calls.append((base_url, path, payload, headers))
        if path.endswith("/attach"):
            return {"ok": True, "lease_token": "lt-issued", "resume_token": "rt-issued", "lease_expires_at_ms": 2000}
        return {"ok": True, "active": True, "lease_expires_at_ms": 2500}

    monkeypatch.setattr(relay_agent, "_post", fake_post)
    assert relay_agent.main(["--config", str(config_path), "run", "--once", "--heartbeat-interval", "0"]) == 0
    assert [call[1] for call in calls] == ["/relay/sessions/tommy/attach", "/relay/devices/heartbeat"]
    saved = json.loads(config_path.read_text())
    assert saved["lease_token"] == "lt-issued"
    assert saved["resume_token"] == "rt-issued"

    service_path = tmp_path / "tommy-relay-agent.service"
    assert relay_agent.main(["--config", str(config_path), "install-user-service", "--dry-run", "--service-path", str(service_path)]) == 0
    service = service_path.read_text()
    assert "tommy-relay-agent" in service
    assert f"--config {config_path}" in service
