#!/usr/bin/env python3
"""Verify that Sophia app hardening patches are present.

Run after applying patchers:

    python voice-agent/scripts/verify_hardening.py

Use --json for machine-readable CI output.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

APP_PATH = Path("voice-agent/src/voice_agent/server/app.py")

CHECKS = {
    "app_reliability": [
        "TrustedSessionStore",
        "build_readiness_report(config)",
        "read_upload_with_limits",
        "VERIFY_AUDIO_POLICY",
    ],
    "graph_outbox": [
        "GraphOutbox",
        '@app.get("/graph/outbox/status")',
        "graph_outbox.summary()",
        "graph_pending",
        "graph_outbox.enqueue",
    ],
    "ui_session_refresh": [
        "sophia_auth_session_v1",
        "hydrateAuthFromCache",
        "refreshTopStatus",
        "Forget trusted session",
    ],
    "memory_sync_ui": [
        'id="memorySync"',
        "refreshMemorySync",
        "pending_total !== undefined",
    ],
    "request_hardening": [
        "install_request_hardening(app)",
        "request_hardening import",
    ],
    "rate_limits": [
        "install_rate_limiter(app)",
        "from .rate_limits import install_rate_limiter",
    ],
    "offline_browser_queue": [
        "sophia_offline_capture_v1",
        'id="offlineQueue"',
        "saveOfflineFirst",
        "syncOfflineQueue",
        "form.append('client_capture_id', record.client_capture_id)",
    ],
    "offline_storage_risk": [
        "OFFLINE_WARN_QUEUE_MB",
        "OFFLINE_MAX_QUEUE_MB",
        'id="offlineProtectStorageBtn"',
        "requestOfflineStoragePersistence",
        "navigator.storage.persist",
        "navigator.storage.persisted",
        "STORAGE RISK",
        "offline queue: storage risk",
    ],
    "offline_reconciliation": [
        "reconcileOfflineRecord(record)",
        "applyServerCaptureResponse(record, data, source = 'upload')",
        "fetch('/capture/by-client-id/'",
        "status: 'reconciling'",
        "reconciled_from: source",
    ],
    "capture_idempotency": [
        "CaptureIdempotencyStore",
        "client_capture_id: str = Form",
        "capture_idempotency.get(client_capture_id_clean)",
        "capture_idempotency.put(client_capture_id_clean, capture_id, response_payload)",
    ],
    "capture_lookup_endpoint": [
        '@app.get("/capture/by-client-id/{client_capture_id}")',
        "async def capture_by_client_id(client_capture_id: str)",
        '"found": False',
        '"found": True',
    ],
    "graph_capture_reconciliation": [
        "lookup_capture_by_client_capture_id",
        '"source": "neo4j"',
        '"source": "idempotency_cache"',
        "graph_lookup.get(\"found\")",
    ],
    "offline_diagnostics": [
        '@app.get("/diagnostics/offline")',
        "async def offline_diagnostics()",
        "capture_idempotency.summary()",
        "idempotency_pruned",
        "browser_indexeddb",
        "durable Sophia memory brain",
    ],
}

# Some checks need alternative markers because the concrete import line is enough.
ALTERNATES = {
    "request_hardening import": ["from .request_hardening import install_request_hardening"],
}


def marker_present(content: str, marker: str) -> bool:
    if marker in content:
        return True
    return any(alt in content for alt in ALTERNATES.get(marker, []))


def verify(content: str) -> dict:
    groups = {}
    ok = True
    for name, markers in CHECKS.items():
        missing = [marker for marker in markers if not marker_present(content, marker)]
        groups[name] = {"ok": not missing, "missing": missing}
        ok = ok and not missing
    return {"ok": ok, "groups": groups}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify Sophia hardening patches in app.py.")
    parser.add_argument("--app-path", default=str(APP_PATH), help="Path to app.py. Defaults to voice-agent/src/voice_agent/server/app.py")
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.app_path)
    if not path.exists():
        payload = {"ok": False, "error": f"Missing app.py: {path}"}
        print(json.dumps(payload) if args.json else payload["error"])
        return 2

    payload = verify(path.read_text(encoding="utf-8"))
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print("Sophia hardening verification")
        for name, result in payload["groups"].items():
            status = "ok" if result["ok"] else "missing"
            print(f"  {name}: {status}")
            for marker in result["missing"]:
                print(f"    - {marker}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
