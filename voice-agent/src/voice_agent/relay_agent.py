from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests


class RelayAgentError(RuntimeError):
    pass


def _device_token(args: argparse.Namespace) -> str | None:
    return args.device_token or os.getenv("TOMMY_RELAY_DEVICE_TOKEN") or os.getenv("SOPHIA_RELAY_DEVICE_TOKEN")


def _admin_headers(args: argparse.Namespace) -> dict[str, str]:
    token = args.admin_token or os.getenv("TOMMY_RELAY_ADMIN_TOKEN") or os.getenv("SOPHIA_RELAY_ADMIN_TOKEN")
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
        args.relay,
        "/relay/devices/register",
        {
            "device_id": args.device_id,
            "name": args.name or args.device_id,
            "owner_id": args.owner_id,
            "capabilities": args.capability,
            "platform": args.platform,
            "mesh_node": args.mesh_node,
            "enrollment_token": args.enrollment_token,
            "fallback_priority": args.fallback_priority,
            "trusted": args.trusted,
        },
    )


def attach(args: argparse.Namespace) -> dict[str, Any]:
    return _post(
        args.relay,
        f"/relay/sessions/{args.session_id}/attach",
        {
            "device_id": args.device_id,
            "owner_id": args.owner_id,
            "device_token": _device_token(args),
            "resume_token": args.resume_token,
            "force": args.force,
        },
    )


def heartbeat(args: argparse.Namespace) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for _ in range(args.count):
        last = _post(
            args.relay,
            "/relay/devices/heartbeat",
            {
                "device_id": args.device_id,
                "device_token": _device_token(args),
                "session_id": args.session_id,
                "lease_token": args.lease_token,
                "seq": args.seq,
            },
        )
        print(json.dumps(last, indent=2))
        if args.count > 1:
            time.sleep(args.interval)
    return last


def send_transcript(args: argparse.Namespace) -> dict[str, Any]:
    return _post(
        args.relay,
        f"/relay/sessions/{args.session_id}/transcript",
        {
            "device_id": args.device_id,
            "device_token": _device_token(args),
            "lease_token": args.lease_token,
            "seq": args.seq,
            "text": args.text,
            "partial": args.partial,
            "source": "agent_cli",
        },
    )


def send_audio_file(args: argparse.Namespace) -> dict[str, Any]:
    data = Path(args.audio_file).read_bytes()
    return _post(
        args.relay,
        f"/relay/sessions/{args.session_id}/audio",
        {
            "device_id": args.device_id,
            "device_token": _device_token(args),
            "lease_token": args.lease_token,
            "seq": args.seq,
            "encoding": args.encoding,
            "payload_b64": base64.b64encode(data).decode("ascii"),
            "byte_count": len(data),
        },
    )


def trust_device(args: argparse.Namespace) -> dict[str, Any]:
    return _post(args.relay, f"/relay/devices/{args.device_id}/trust", {"trusted": args.trusted}, _admin_headers(args))


def rotate_device_token(args: argparse.Namespace) -> dict[str, Any]:
    return _post(args.relay, f"/relay/devices/{args.device_id}/rotate-token", {}, _admin_headers(args))


def revoke_device(args: argparse.Namespace) -> dict[str, Any]:
    return _post(args.relay, f"/relay/devices/{args.device_id}/revoke", {"reason": args.reason}, _admin_headers(args))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tommy telescope relay agent CLI")
    parser.add_argument("--relay", default="http://127.0.0.1:8765", help="Sophia voice-agent base URL")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--owner-id", default="scott")
    parser.add_argument("--session-id", default="tommy")
    parser.add_argument("--device-token", default=None)
    parser.add_argument("--admin-token", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    reg = sub.add_parser("register")
    reg.add_argument("--name", default="")
    reg.add_argument("--capability", action="append", default=["mic", "speaker"])
    reg.add_argument("--platform", default=sys.platform)
    reg.add_argument("--mesh-node", default="")
    reg.add_argument("--enrollment-token", default=None)
    reg.add_argument("--fallback-priority", type=int, default=100)
    reg.add_argument("--trusted", action="store_true")
    reg.set_defaults(func=register)

    att = sub.add_parser("attach")
    att.add_argument("--resume-token", default=None)
    att.add_argument("--force", action="store_true")
    att.set_defaults(func=attach)

    hb = sub.add_parser("heartbeat")
    hb.add_argument("--lease-token", required=True)
    hb.add_argument("--seq", type=int, default=None)
    hb.add_argument("--interval", type=float, default=5)
    hb.add_argument("--count", type=int, default=1)
    hb.set_defaults(func=heartbeat)

    tx = sub.add_parser("transcript")
    tx.add_argument("--lease-token", required=True)
    tx.add_argument("--seq", type=int, required=True)
    tx.add_argument("--text", required=True)
    tx.add_argument("--partial", action="store_true")
    tx.set_defaults(func=send_transcript)

    aud = sub.add_parser("audio-file")
    aud.add_argument("--lease-token", required=True)
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
    except RelayAgentError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
