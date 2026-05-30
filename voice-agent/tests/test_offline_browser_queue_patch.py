from pathlib import Path
import shutil
import subprocess
import sys


def test_offline_browser_queue_patch_is_idempotent(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    src = repo / "voice-agent" / "src" / "voice_agent" / "server" / "app.py"
    script = repo / "voice-agent" / "scripts" / "patch_offline_browser_queue.py"

    work = tmp_path / "work"
    target = work / "voice-agent" / "src" / "voice_agent" / "server"
    target.mkdir(parents=True)
    shutil.copy2(src, target / "app.py")

    scripts = work / "voice-agent" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(script, scripts / "patch_offline_browser_queue.py")

    subprocess.run(
        [sys.executable, "voice-agent/scripts/patch_offline_browser_queue.py"],
        cwd=work,
        check=True,
    )
    patched = (target / "app.py").read_text(encoding="utf-8")

    assert "sophia_offline_capture_v1" in patched
    assert 'id="offlineQueue"' in patched
    assert 'id="offlineQueueSection"' in patched
    assert 'id="offlineSyncBtn"' in patched
    assert 'id="offlineExportBtn"' in patched
    assert 'id="offlineDeleteSyncedBtn"' in patched
    assert "function openOfflineDb()" in patched
    assert "function buildOfflineCaptureRecord()" in patched
    assert "function captureRecordToForm(record)" in patched
    assert "async function saveOfflineFirst()" in patched
    assert "async function syncOfflineQueue" in patched
    assert "async function reconcileOfflineRecord(record)" in patched
    assert "async function applyServerCaptureResponse(record, data, source = 'upload')" in patched
    assert "fetch('/capture/by-client-id/'" in patched
    assert "status: 'reconciling'" in patched
    assert "reconciled_from: source" in patched
    assert "installOfflineQueueHandlers()" in patched
    assert "form.append('client_capture_id', record.client_capture_id)" in patched
    assert "client_capture_id: str = Form" in patched
    assert "client_capture_id_clean" in patched
    assert "IndexedDB" in patched

    second = subprocess.run(
        [sys.executable, "voice-agent/scripts/patch_offline_browser_queue.py"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "already applied" in second.stdout
    assert (target / "app.py").read_text(encoding="utf-8") == patched
