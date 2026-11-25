#!/usr/bin/env python3
"""Probe a REAL AssistX voice-event endpoint (PLAN.md Phase 8, item 4).

``tests/test_dispatch_bridge_auto_config.py`` proves the Sophia -> AssistX wire
contract against a stubbed ``httpx``. This script is the other half: it talks to
an actual AssistX instance with an actual HMAC signature and reports what
happened. It never invents a result -- every prerequisite is checked first and a
missing prerequisite produces ``status: "skipped"`` with the exact reason and the
command that fixes it.

What it checks, in order:

1. The webhook base URL is *explicitly* configured
   (``ASSISTX_VOICE_WEBHOOK_BASE_URL``). The library default
   ``http://host.docker.internal:8000`` is a container placeholder, so it counts
   as unconfigured rather than "reachable".
2. A webhook secret is configured (``ASSISTX_VOICE_WEBHOOK_SECRET`` or
   ``VOICE_WEBHOOK_SECRET``), otherwise the signature is empty and every real
   AssistX rejects the event.
3. A signed, review-safe probe event (``task_proposed``, ``auto_dispatch=false``)
   is accepted. This event cannot create a task in AssistX, so the probe is safe
   to run against production.
4. Optionally (``--confirm-side-effects``) an actionable event
   (``task_created``, ``auto_dispatch=true``) is dispatched. This DOES create a
   real AssistX task, so it is opt-in and reported separately.
5. The AssistX trace endpoint answers for the correlation id.
6. If a Sophia server is reachable, ``GET /dispatch/status`` is cross-checked
   against the direct probe so the console pill cannot claim AssistX is healthy
   when it is not.

Examples::

    export ASSISTX_VOICE_WEBHOOK_BASE_URL=http://127.0.0.1:8000
    export ASSISTX_VOICE_WEBHOOK_SECRET="$(openssl rand -hex 20)"   # must match AssistX
    python scripts/dispatch_live_probe.py --out-dir ../reports/phase8/dispatch
    python scripts/dispatch_live_probe.py --confirm-side-effects --json

Exit codes: 0 probe passed, 1 the real endpoint rejected us or was unreachable
when it was configured, 2 usage error, 3 skipped (prerequisite missing).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from voice_agent.server.assistx_dispatch import (  # noqa: E402
    AUTHENTICATED_SCOTT,
    assistx_base_url,
    assistx_voice_event_payload,
    build_voice_event,
    dispatch_to_assistx,
    json_dumps,
    sign_headers,
)

REPORT_SCHEMA = "sophia.dispatch_live_probe.v1"
PLACEHOLDER_BASE_URLS = {"http://host.docker.internal:8000", "http://host.docker.internal:8000/"}
PROBE_TEXT = "sophia dispatch hub connectivity probe (review-safe, creates no task)"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _skipped(reason: str, fix: list[str], detail: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "status": "skipped",
        "skip_reason": reason,
        "fix": fix,
        "generated_at": _now(),
        "detail": detail or {},
        "warnings": [],
        "phase8_evidence": False,
        "phase8_evidence_note": (
            "a skipped run is never Phase 8 evidence; it needs ASSISTX_VOICE_WEBHOOK_BASE_URL and a "
            "matching ASSISTX_VOICE_WEBHOOK_SECRET for the real instance"
        ),
        "reliability_note": (
            "No AssistX round-trip was performed, so nothing here is evidence that the "
            "dispatch hub works against a real instance."
        ),
    }


def _resolve_base_url(raw: str | None) -> tuple[str | None, str]:
    """Return ``(base_url, reason)``; ``base_url`` is None when unconfigured."""
    if raw:
        return assistx_base_url(raw), ""
    if "ASSISTX_VOICE_WEBHOOK_BASE_URL" not in os.environ:
        return None, "ASSISTX_VOICE_WEBHOOK_BASE_URL is not set (the built-in default is a container placeholder)"
    configured = assistx_base_url()
    if configured in PLACEHOLDER_BASE_URLS:
        return None, f"ASSISTX_VOICE_WEBHOOK_BASE_URL is still the placeholder {configured!r}"
    return configured, ""


def _resolve_secret(raw: str | None) -> tuple[str, str]:
    if raw:
        return raw, ""
    if "ASSISTX_VOICE_WEBHOOK_SECRET" not in os.environ and "VOICE_WEBHOOK_SECRET" not in os.environ:
        return "", "neither ASSISTX_VOICE_WEBHOOK_SECRET nor VOICE_WEBHOOK_SECRET is set"
    return os.getenv("ASSISTX_VOICE_WEBHOOK_SECRET") or os.getenv("VOICE_WEBHOOK_SECRET") or "", ""


def _probe_event(webhook_url: str, secret: str, timeout: float) -> dict[str, Any]:
    """Send the review-safe signed probe and capture the raw outcome."""
    event = build_voice_event(
        "task_proposed",
        text=PROBE_TEXT,
        metadata={"phase8_probe": True},
        auto_dispatch=False,
        actor={"user_id": "scott", "auth_state": AUTHENTICATED_SCOTT},
    )
    wire = assistx_voice_event_payload(event)
    body = json_dumps(wire)
    headers = sign_headers(secret, body)
    started = time.perf_counter()
    outcome: dict[str, Any] = {
        "event_type_on_wire": wire.get("event_type"),
        "auto_dispatch": wire.get("auto_dispatch"),
        "authorization_action": (event.get("metadata") or {}).get("authorization_action"),
        "signature_header": next(iter(k for k in headers if k != "Content-Type"), None),
        "status_code": None,
        "response": None,
        "error": None,
        "latency_ms": None,
    }
    try:
        response = httpx.post(webhook_url, content=body, headers=headers, timeout=timeout)
    except Exception as exc:
        outcome["error"] = f"{type(exc).__name__}: {exc}"
    else:
        outcome["status_code"] = response.status_code
        try:
            outcome["response"] = response.json()
        except Exception:
            outcome["response"] = response.text[:500]
    outcome["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return outcome


def _trace(base_url: str, correlation_id: str, timeout: float) -> dict[str, Any]:
    trace_url = f"{base_url}/api/traces/{correlation_id}"
    auth_user = os.getenv("ASSISTX_BASIC_AUTH_USER", "")
    auth_pass = os.getenv("ASSISTX_BASIC_AUTH_PASS", "")
    auth = (auth_user, auth_pass) if auth_user and auth_pass else None
    try:
        response = httpx.get(trace_url, timeout=timeout, auth=auth)
    except Exception as exc:
        return {"url": trace_url, "reachable": False, "error": f"{type(exc).__name__}: {exc}"}
    body: Any
    try:
        body = response.json()
    except Exception:
        body = response.text[:500]
    return {
        "url": trace_url,
        "reachable": True,
        "status_code": response.status_code,
        "events": (body.get("events") if isinstance(body, dict) else None),
        "current_state": body.get("current_state") if isinstance(body, dict) else None,
    }


def _server_cross_check(server_base_url: str, timeout: float, comparable: bool) -> dict[str, Any]:
    url = f"{server_base_url.rstrip('/')}/dispatch/status"
    if not comparable:
        return {
            "checked": False,
            "url": url,
            "reason": (
                "not comparable: this probe was pointed at a different base URL / secret than the "
                "running server's environment, so a different verdict is expected"
            ),
        }
    try:
        response = httpx.get(url, timeout=timeout)
    except Exception as exc:
        return {"checked": False, "url": url, "error": f"{type(exc).__name__}: {exc}"}
    if response.status_code != 200:
        return {"checked": True, "url": url, "status_code": response.status_code}
    body = response.json()
    return {
        "checked": True,
        "url": url,
        "assistx_url": body.get("assistx_url"),
        "assistx_reachable": body.get("assistx_reachable"),
        "assistx_webhook_ok": body.get("assistx_webhook_ok"),
        "assistx_webhook_status": body.get("assistx_webhook_status"),
        "agrees_with_direct_probe": None,
    }


def _apply_library_override(base_url: str, secret: str) -> None:
    """Point the in-process dispatch library at the operator-supplied target.

    ``server.assistx_dispatch`` captures ``ASSISTX_VOICE_WEBHOOK_BASE_URL`` and
    ``ASSISTX_VOICE_WEBHOOK_SECRET`` at *import* time, so a harness that passes
    ``--base-url`` / ``--secret`` must rebind them explicitly or the actionable
    dispatch would silently go to the configured (or placeholder) endpoint with
    the configured (or empty) secret.
    """
    import voice_agent.server.assistx_dispatch as dispatch_mod

    dispatch_mod.ASSISTX_VOICE_WEBHOOK_BASE_URL = base_url
    dispatch_mod.ASSISTX_VOICE_WEBHOOK_BASE_URL_CONFIGURED = True
    dispatch_mod.ASSISTX_VOICE_WEBHOOK_SECRET = secret
    dispatch_mod.ASSISTX_VOICE_WEBHOOK_SECRET_CONFIGURED = True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe a real AssistX voice-event endpoint.")
    parser.add_argument("--base-url", default=None, help="AssistX base URL (default: $ASSISTX_VOICE_WEBHOOK_BASE_URL).")
    parser.add_argument("--secret", default=None, help="Webhook HMAC secret (default: $ASSISTX_VOICE_WEBHOOK_SECRET).")
    parser.add_argument("--server-base-url", default="http://127.0.0.1:8765", help="Sophia server for the /dispatch/status cross-check.")
    parser.add_argument("--skip-server-check", action="store_true", help="Do not cross-check a running Sophia server.")
    parser.add_argument("--confirm-side-effects", action="store_true", help="Also send an actionable task_created event (creates a REAL AssistX task).")
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-request timeout in seconds.")
    parser.add_argument("--out-dir", default=None, help="Write dispatch_live_probe.json here.")
    parser.add_argument("--json", action="store_true", help="Print the JSON report instead of the summary.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    base_url, url_reason = _resolve_base_url(args.base_url)
    if base_url is None:
        report = _skipped(
            f"AssistX webhook base URL is not configured: {url_reason}",
            [
                "export ASSISTX_VOICE_WEBHOOK_BASE_URL=http://<assistx-host>:8000",
                "python scripts/dispatch_live_probe.py --base-url http://<assistx-host>:8000",
            ],
        )
        return _emit(report, args)
    secret, secret_reason = _resolve_secret(args.secret)
    if not secret:
        report = _skipped(
            f"AssistX webhook secret is not configured: {secret_reason}",
            [
                "openssl rand -hex 20   # then set the SAME value as ASSISTX_VOICE_WEBHOOK_SECRET in both services",
                "export ASSISTX_VOICE_WEBHOOK_SECRET=<shared-secret>",
            ],
            detail={"assistx_url": base_url},
        )
        return _emit(report, args)

    using_overrides = bool(args.base_url or args.secret)
    _apply_library_override(base_url, secret)
    webhook_url = f"{base_url}/api/voice/events"
    probe = _probe_event(webhook_url, secret, args.timeout)
    correlation_id = (probe.get("response") or {}).get("correlation_id") if isinstance(probe.get("response"), dict) else None
    warnings: list[str] = []
    if probe.get("error"):
        status = "failed"
        reason = f"AssistX webhook {webhook_url} could not be reached: {probe['error']}"
    elif probe.get("status_code") != 200:
        status = "failed"
        reason = f"AssistX webhook {webhook_url} rejected the signed probe with HTTP {probe.get('status_code')}"
        warnings.append("a real AssistX rejecting X-Voice-Signature means the shared secret or the wire model drifted")
    else:
        status = "ok"
        reason = "signed review-safe probe accepted by the real AssistX endpoint"

    dispatch: dict[str, Any] = {"attempted": False}
    if args.confirm_side_effects and status == "ok":
        event = build_voice_event(
            "task_created",
            text="Sophia Phase 8 dispatch-hub validation event",
            metadata={"phase8_probe": True, "phase8_side_effect": True},
            auto_dispatch=True,
            actor={"user_id": "scott", "auth_state": AUTHENTICATED_SCOTT},
        )
        dispatch = {"attempted": True, **dispatch_to_assistx(event, timeout=args.timeout)}
        if not dispatch.get("sent"):
            status = "failed"
            reason = f"actionable dispatch was not accepted: {dispatch.get('error')}"
    elif not args.confirm_side_effects:
        warnings.append(
            "actionable task_created dispatch not attempted; pass --confirm-side-effects to exercise the "
            "executable path (it creates a real AssistX task)"
        )

    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "status": status,
        "skip_reason": None,
        "reason": reason,
        "generated_at": _now(),
        "detail": {
            "assistx_url": base_url,
            "webhook_url": webhook_url,
            "probe": probe,
            "actionable_dispatch": dispatch,
            "trace": _trace(base_url, correlation_id or "unknown", args.timeout) if correlation_id else None,
        },
        "warnings": warnings,
        "fix": [],
        "phase8_evidence": status == "ok",
        "phase8_evidence_note": (
            "counts as Phase 8 evidence only when the REAL AssistX endpoint accepted a signed event "
            "and Sophia's own /dispatch/status agrees"
        ),
    }
    if not args.skip_server_check:
        cross = _server_cross_check(args.server_base_url, args.timeout, comparable=not using_overrides)
        if cross.get("checked") and cross.get("assistx_webhook_ok") is not None:
            direct_ok = probe.get("status_code") == 200
            cross["agrees_with_direct_probe"] = bool(cross["assistx_webhook_ok"]) == bool(direct_ok)
            if not cross["agrees_with_direct_probe"]:
                report["status"] = "failed"
                report["reason"] = (
                    f"{report['reason']}; the running Sophia server's /dispatch/status disagrees "
                    f"(webhook_ok={cross['assistx_webhook_ok']} vs direct probe ok={direct_ok})"
                )
                report["warnings"].append(
                    "the console AssistX pill can report a different verdict than a direct signed probe"
                )
        report["detail"]["server_cross_check"] = cross
    return _emit(report, args)


def _emit(report: dict[str, Any], args: argparse.Namespace) -> int:
    if args.out_dir:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "dispatch_live_probe.json"
        path.write_text(json.dumps(report, indent=2, default=str))
        report = {**report, "report_path": str(path)}
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(_summary(report))
    if report["status"] == "skipped":
        return 3
    return 0 if report["status"] == "ok" else 1


def _summary(report: dict[str, Any]) -> str:
    lines = [
        "ASSISTX LIVE DISPATCH PROBE",
        f"  schema   : {report['schema']}",
        f"  status   : {report['status'].upper()}",
        f"  generated: {report.get('generated_at')}",
    ]
    if report.get("reason"):
        lines.append(f"  verdict  : {report['reason']}")
    if report.get("skip_reason"):
        lines.append(f"  skipped  : {report['skip_reason']}")
    probe = (report.get("detail") or {}).get("probe") or {}
    if probe:
        lines.append(
            "  probe    : "
            f"event_type_on_wire={probe.get('event_type_on_wire')} "
            f"auto_dispatch={probe.get('auto_dispatch')} "
            f"auth_action={probe.get('authorization_action')} "
            f"status={probe.get('status_code')} latency_ms={probe.get('latency_ms')}"
        )
    dispatch = (report.get("detail") or {}).get("actionable_dispatch") or {}
    if dispatch.get("attempted"):
        lines.append(
            f"  dispatch : sent={dispatch.get('sent')} error={dispatch.get('error')} "
            f"correlation_id={dispatch.get('correlation_id')}"
        )
    cross = (report.get("detail") or {}).get("server_cross_check") or {}
    if cross.get("checked"):
        lines.append(
            f"  ui pill  : webhook_ok={cross.get('assistx_webhook_ok')} "
            f"agrees_with_direct_probe={cross.get('agrees_with_direct_probe')}"
        )
    for warning in report.get("warnings") or []:
        lines.append(f"  ! {warning}")
    for command in report.get("fix") or []:
        lines.append(f"  -> {command}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
