import json
import subprocess
import sys
from pathlib import Path


def test_verify_hardening_json_reports_groups():
    repo = Path(__file__).resolve().parents[2]
    script = repo / "voice-agent" / "scripts" / "verify_hardening.py"

    result = subprocess.run(
        [sys.executable, str(script), "--json"],
        cwd=repo,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert "ok" in payload
    assert "groups" in payload
    assert "offline_browser_queue" in payload["groups"]
    assert "offline_reconciliation" in payload["groups"]
    assert "capture_idempotency" in payload["groups"]
    assert "capture_lookup_endpoint" in payload["groups"]


def test_verify_hardening_can_pass_with_all_markers(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    script = repo / "voice-agent" / "scripts" / "verify_hardening.py"
    app = tmp_path / "app.py"
    app.write_text(
        "\n".join(
            [
                "TrustedSessionStore",
                "build_readiness_report(config)",
                "read_upload_with_limits",
                "VERIFY_AUDIO_POLICY",
                "GraphOutbox",
                '@app.get("/graph/outbox/status")',
                "graph_outbox.summary()",
                "graph_pending",
                "graph_outbox.enqueue",
                "sophia_auth_session_v1",
                "hydrateAuthFromCache",
                "refreshTopStatus",
                "Forget trusted session",
                'id="memorySync"',
                "refreshMemorySync",
                "pending_total !== undefined",
                "install_request_hardening(app)",
                "from .request_hardening import install_request_hardening",
                "install_rate_limiter(app)",
                "from .rate_limits import install_rate_limiter",
                "sophia_offline_capture_v1",
                'id="offlineQueue"',
                "saveOfflineFirst",
                "syncOfflineQueue",
                "form.append('client_capture_id', record.client_capture_id)",
                "reconcileOfflineRecord(record)",
                "applyServerCaptureResponse(record, data, source = 'upload')",
                "fetch('/capture/by-client-id/'",
                "status: 'reconciling'",
                "reconciled_from: source",
                "CaptureIdempotencyStore",
                "client_capture_id: str = Form",
                "capture_idempotency.get(client_capture_id_clean)",
                "capture_idempotency.put(client_capture_id_clean, capture_id, response_payload)",
                '@app.get("/capture/by-client-id/{client_capture_id}")',
                "async def capture_by_client_id(client_capture_id: str)",
                '"found": False',
                '"found": True',
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(script), "--app-path", str(app), "--json"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert all(group["ok"] for group in payload["groups"].values())
