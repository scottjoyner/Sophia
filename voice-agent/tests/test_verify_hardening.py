import json
import subprocess
import sys
from pathlib import Path
import pytest
pytest.skip("Legacy hardening patcher tests retired; runtime coverage is kept.", allow_module_level=True)


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
    assert "offline_storage_risk" in payload["groups"]
    assert "offline_reconciliation" in payload["groups"]
    assert "capture_idempotency" in payload["groups"]
    assert "capture_lookup_endpoint" in payload["groups"]
    assert "offline_diagnostics" in payload["groups"]


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
                "OFFLINE_WARN_QUEUE_MB",
                "OFFLINE_MAX_QUEUE_MB",
                'id="offlineProtectStorageBtn"',
                "requestOfflineStoragePersistence",
                "navigator.storage.persist",
                "navigator.storage.persisted",
                "STORAGE RISK",
                "offline queue: storage risk",
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
                '@app.get("/diagnostics/offline")',
                "async def offline_diagnostics()",
                "capture_idempotency.summary()",
                "idempotency_pruned",
                "browser_indexeddb",
                "durable Sophia memory brain",
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
