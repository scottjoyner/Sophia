from __future__ import annotations

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
