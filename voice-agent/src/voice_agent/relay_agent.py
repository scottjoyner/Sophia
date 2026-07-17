from __future__ import annotations

import argparse
import base64
import json
import os
import shlex
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import requests


class RelayAgentError(RuntimeError):
    pass


def default_config_path() -> Path:
    return Path(os.getenv("TOMMY_RELAY_AGENT_CONFIG", "~/.config/tommy-relay-agent/config.json")).expanduser()


def _load_config(path: str | Path | None) -> dict[str, Any]:
    config_path = Path(path).expanduser() if path else default_config_path()
    if not config_path.exists():
        return {}
    return json.loads(config_path.read_text())


def _save_config(path: str | Path | None, data: dict[str, Any], args: argparse.Namespace | None = None) -> Path:
    config_path = Path(path).expanduser() if path else default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=config_path.name + ".", suffix=".tmp", dir=config_path.parent)
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(payload)
        os.replace(tmp_path, config_path)
        config_path.chmod(0o600)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
    if args is not None:
        args.config_data = data
    return config_path


def _cfg(args: argparse.Namespace) -> dict[str, Any]:
    config = getattr(args, "config_data", None)
    if config is None:
        config = _load_config(getattr(args, "config", None))
        args.config_data = config
    return config


def _arg_or_config(args: argparse.Namespace, arg_name: str, config_name: str | None = None, default: Any = None) -> Any:
    value = getattr(args, arg_name, None)
    if value not in (None, "", []):
        return value
    return _cfg(args).get(config_name or arg_name, default)


def _relay_url(args: argparse.Namespace) -> str:
    return _arg_or_config(args, "relay", "relay_url", "http://127.0.0.1:8765")


def _device_id(args: argparse.Namespace) -> str:
    value = _arg_or_config(args, "device_id")
    if not value:
        raise RelayAgentError("Missing device id; pass --device-id or enroll/configure this device")
    return str(value)


def _session_id(args: argparse.Namespace) -> str:
    return str(_arg_or_config(args, "session_id", default="tommy"))


def _device_token(args: argparse.Namespace) -> str | None:
    return (
        args.device_token
        or os.getenv("TOMMY_RELAY_DEVICE_TOKEN")
        or os.getenv("SOPHIA_RELAY_DEVICE_TOKEN")
        or _cfg(args).get("device_token")
    )


def _lease_token(args: argparse.Namespace) -> str | None:
    return getattr(args, "lease_token", None) or _cfg(args).get("lease_token")


def _admin_headers(args: argparse.Namespace) -> dict[str, str]:
    token = args.admin_token or os.getenv("TOMMY_RELAY_ADMIN_TOKEN") or os.getenv("SOPHIA_RELAY_ADMIN_TOKEN") or _cfg(args).get("admin_token")
    if not token:
        raise RelayAgentError("Missing relay admin token; pass --admin-token or set TOMMY_RELAY_ADMIN_TOKEN")
    return {"x-relay-admin-token": token}


def _post(base_url: str, path: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    response = requests.post(url, json=payload, headers=headers, timeout=30)
    try:
        body = response.json()
    except Exception:
        body = {"text": response.text}
    if response.status_code >= 400:
        raise RelayAgentError(f"{response.status_code} {url}: {body}")
    return body


def register(args: argparse.Namespace) -> dict[str, Any]:
    return _post(
        _relay_url(args),
        "/relay/devices/register",
        {
            "device_id": _device_id(args),
            "name": args.name or _device_id(args),
            "owner_id": args.owner_id,
            "capabilities": args.capability,
            "platform": args.platform,
            "mesh_node": args.mesh_node,
            "enrollment_token": args.enrollment_token,
            "fallback_priority": args.fallback_priority,
            "trusted": args.trusted,
            "device_token": _device_token(args),
            "audio_source": getattr(args, "audio_source", ""),
            "tailscale_ip": getattr(args, "tailscale_ip", ""),
            "location": getattr(args, "location", ""),
        },
    )


def enroll(args: argparse.Namespace) -> dict[str, Any]:
    result = register(args)
    config = {**_cfg(args)}
    config.update(
        {
            "relay_url": _relay_url(args),
            "device_id": result.get("device_id", _device_id(args)),
            "device_token": result.get("device_token", _device_token(args)),
            "session_id": _session_id(args),
            "owner_id": args.owner_id,
            "capabilities": args.capability,
            "platform": args.platform,
            "mesh_node": args.mesh_node,
            "fallback_priority": args.fallback_priority,
        }
    )
    if getattr(args, "audio_source", ""):
        config["audio_source"] = args.audio_source
    path = _save_config(args.config, {k: v for k, v in config.items() if v not in (None, "")}, args)
    result["config_path"] = str(path)
    return result


def attach(args: argparse.Namespace) -> dict[str, Any]:
    return _post(
        _relay_url(args),
        f"/relay/sessions/{_session_id(args)}/attach",
        {
            "device_id": _device_id(args),
            "owner_id": args.owner_id,
            "device_token": _device_token(args),
            "resume_token": args.resume_token or _cfg(args).get("resume_token"),
            "force": args.force,
        },
    )


def resume(args: argparse.Namespace) -> dict[str, Any]:
    resume_token = args.resume_token or _cfg(args).get("resume_token")
    if not resume_token:
        raise RelayAgentError("Missing resume token; pass --resume-token or attach first")
    return _post(
        _relay_url(args),
        f"/relay/sessions/{_session_id(args)}/resume",
        {
            "device_id": _device_id(args),
            "device_token": _device_token(args),
            "resume_token": resume_token,
            "last_seen_event_id": args.last_seen_event_id,
            "last_seen_seq": args.last_seen_seq,
        },
    )


def heartbeat(args: argparse.Namespace) -> dict[str, Any]:
    lease_token = _lease_token(args)
    if not lease_token:
        raise RelayAgentError("Missing lease token; pass --lease-token or attach/run first")
    last: dict[str, Any] = {}
    for _ in range(args.count):
        last = _post(
            _relay_url(args),
            "/relay/devices/heartbeat",
            {
                "device_id": _device_id(args),
                "device_token": _device_token(args),
                "session_id": _session_id(args),
                "lease_token": lease_token,
                "seq": args.seq,
            },
        )
        if args.count > 1:
            print(json.dumps(last, indent=2))
            time.sleep(args.interval)
    return last


def status(args: argparse.Namespace) -> dict[str, Any]:
    args.count = 1
    args.interval = 0
    return heartbeat(args)


def send_transcript(args: argparse.Namespace) -> dict[str, Any]:
    return _post(
        _relay_url(args),
        f"/relay/sessions/{_session_id(args)}/transcript",
        {
            "device_id": _device_id(args),
            "device_token": _device_token(args),
            "lease_token": _lease_token(args),
            "seq": args.seq,
            "text": args.text,
            "partial": args.partial,
            "source": "agent_cli",
        },
    )


def send_audio_file(args: argparse.Namespace) -> dict[str, Any]:
    data = Path(args.audio_file).read_bytes()
    return _post(
        _relay_url(args),
        f"/relay/sessions/{_session_id(args)}/audio",
        {
            "device_id": _device_id(args),
            "device_token": _device_token(args),
            "lease_token": _lease_token(args),
            "seq": args.seq,
            "encoding": args.encoding,
            "payload_b64": base64.b64encode(data).decode("ascii"),
            "byte_count": len(data),
        },
    )


def trust_device(args: argparse.Namespace) -> dict[str, Any]:
    return _post(_relay_url(args), f"/relay/devices/{_device_id(args)}/trust", {"trusted": args.trusted}, _admin_headers(args))


def rotate_device_token(args: argparse.Namespace) -> dict[str, Any]:
    result = _post(_relay_url(args), f"/relay/devices/{_device_id(args)}/rotate-token", {}, _admin_headers(args))
    if result.get("device_token"):
        config = {**_cfg(args), "device_token": result["device_token"]}
        _save_config(args.config, config, args)
    return result


def revoke_device(args: argparse.Namespace) -> dict[str, Any]:
    return _post(_relay_url(args), f"/relay/devices/{_device_id(args)}/revoke", {"reason": args.reason}, _admin_headers(args))


def run_daemon(args: argparse.Namespace) -> dict[str, Any]:
    config = {**_cfg(args)}
    lease_token = _lease_token(args)
    if not lease_token:
        attached = attach(args)
        lease_token = attached.get("lease_token")
        config.update({k: attached[k] for k in ("lease_token", "resume_token", "lease_expires_at_ms") if k in attached})
        _save_config(args.config, {**config, "relay_url": _relay_url(args), "device_id": _device_id(args), "device_token": _device_token(args), "session_id": _session_id(args)}, args)
    args.lease_token = lease_token
    args.count = 1
    args.interval = 0
    last: dict[str, Any] = {}
    while True:
        last = heartbeat(args)
        if not last.get("active"):
            resumed = resume(args) if _cfg(args).get("resume_token") else attach(args)
            config.update({k: resumed[k] for k in ("lease_token", "resume_token", "lease_expires_at_ms") if k in resumed})
            _save_config(args.config, {**_cfg(args), **config}, args)
            args.lease_token = config.get("lease_token")
        if args.once:
            return last
        time.sleep(args.heartbeat_interval)


def install_user_service(args: argparse.Namespace) -> dict[str, Any]:
    config_path = Path(args.config).expanduser() if args.config else default_config_path()
    service_path = Path(args.service_path).expanduser() if args.service_path else Path("~/.config/systemd/user/tommy-relay-agent.service").expanduser()
    service = f"""[Unit]
Description=tommy-relay-agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={shlex.quote(sys.executable)} -m voice_agent.relay_agent --config {shlex.quote(str(config_path))} run
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text(service)
    result = {"ok": True, "service_path": str(service_path), "dry_run": args.dry_run}
    if not args.dry_run:
        result["next_steps"] = ["systemctl --user daemon-reload", "systemctl --user enable --now tommy-relay-agent.service"]
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tommy telescope relay agent CLI")
    parser.add_argument("--config", default=None, help="Local relay agent JSON config path")
    parser.add_argument("--relay", default=None, help="Sophia voice-agent base URL")
    parser.add_argument("--device-id", default=None)
    parser.add_argument("--owner-id", default="scott")
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--device-token", default=None)
    parser.add_argument("--admin-token", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_register_options(cmd: argparse.ArgumentParser) -> None:
        cmd.add_argument("--name", default="")
        cmd.add_argument("--capability", action="append", default=[])
        cmd.add_argument("--platform", default=sys.platform)
        cmd.add_argument("--mesh-node", default="")
        cmd.add_argument("--enrollment-token", default=None)
        cmd.add_argument("--fallback-priority", type=int, default=100)
        cmd.add_argument("--trusted", action="store_true")
        cmd.add_argument("--audio-source", default="")
        cmd.add_argument("--tailscale-ip", default="")
        cmd.add_argument("--location", default="")

    reg = sub.add_parser("register")
    add_register_options(reg)
    reg.set_defaults(func=register)

    enr = sub.add_parser("enroll")
    add_register_options(enr)
    enr.set_defaults(func=enroll)

    att = sub.add_parser("attach")
    att.add_argument("--resume-token", default=None)
    att.add_argument("--force", action="store_true")
    att.set_defaults(func=attach)

    res = sub.add_parser("resume")
    res.add_argument("--resume-token", default=None)
    res.add_argument("--last-seen-event-id", type=int, default=0)
    res.add_argument("--last-seen-seq", type=int, default=None)
    res.set_defaults(func=resume)

    hb = sub.add_parser("heartbeat")
    hb.add_argument("--lease-token", default=None)
    hb.add_argument("--seq", type=int, default=None)
    hb.add_argument("--interval", type=float, default=5)
    hb.add_argument("--count", type=int, default=1)
    hb.set_defaults(func=heartbeat)

    stat = sub.add_parser("status")
    stat.add_argument("--lease-token", default=None)
    stat.add_argument("--seq", type=int, default=None)
    stat.set_defaults(func=status)

    run = sub.add_parser("run")
    run.add_argument("--lease-token", default=None)
    run.add_argument("--resume-token", default=None)
    run.add_argument("--force", action="store_true")
    run.add_argument("--seq", type=int, default=None)
    run.add_argument("--heartbeat-interval", type=float, default=5)
    run.add_argument("--once", action="store_true")
    run.set_defaults(func=run_daemon)

    tx = sub.add_parser("transcript")
    tx.add_argument("--lease-token", default=None)
    tx.add_argument("--seq", type=int, required=True)
    tx.add_argument("--text", required=True)
    tx.add_argument("--partial", action="store_true")
    tx.set_defaults(func=send_transcript)

    aud = sub.add_parser("audio-file")
    aud.add_argument("--lease-token", default=None)
    aud.add_argument("--seq", type=int, required=True)
    aud.add_argument("--audio-file", required=True)
    aud.add_argument("--encoding", default="pcm_s16le")
    aud.set_defaults(func=send_audio_file)

    trust = sub.add_parser("trust")
    trust.add_argument("--trusted", action="store_true", default=True)
    trust.add_argument("--untrusted", dest="trusted", action="store_false")
    trust.set_defaults(func=trust_device)

    rotate = sub.add_parser("rotate-token")
    rotate.set_defaults(func=rotate_device_token)

    revoke = sub.add_parser("revoke")
    revoke.add_argument("--reason", default="revoked")
    revoke.set_defaults(func=revoke_device)

    svc = sub.add_parser("install-user-service")
    svc.add_argument("--dry-run", action="store_true")
    svc.add_argument("--service-path", default=None)
    svc.set_defaults(func=install_user_service)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.config_data = _load_config(args.config)
    if hasattr(args, "capability") and not args.capability:
        args.capability = args.config_data.get("capabilities") or ["mic", "speaker"]
    try:
        result = args.func(args)
    except RelayAgentError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
