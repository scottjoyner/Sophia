from pathlib import Path
import shutil
import subprocess
import sys


def _copy_patch_script(repo: Path, work: Path, name: str) -> None:
    scripts = work / "voice-agent" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    shutil.copy2(repo / "voice-agent" / "scripts" / name, scripts / name)


def test_offline_storage_risk_patch_after_offline_queue_patch(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    src = repo / "voice-agent" / "src" / "voice_agent" / "server" / "app.py"

    work = tmp_path / "work"
    target = work / "voice-agent" / "src" / "voice_agent" / "server"
    target.mkdir(parents=True)
    shutil.copy2(src, target / "app.py")

    _copy_patch_script(repo, work, "patch_offline_browser_queue.py")
    _copy_patch_script(repo, work, "patch_offline_storage_risk.py")

    subprocess.run(
        [sys.executable, "voice-agent/scripts/patch_offline_browser_queue.py"],
        cwd=work,
        check=True,
    )
    subprocess.run(
        [sys.executable, "voice-agent/scripts/patch_offline_storage_risk.py"],
        cwd=work,
        check=True,
    )
    patched = (target / "app.py").read_text(encoding="utf-8")

    assert "OFFLINE_WARN_QUEUE_MB" in patched
    assert "OFFLINE_MAX_QUEUE_MB" in patched
    assert 'id="offlineProtectStorageBtn"' in patched
    assert "requestOfflineStoragePersistence" in patched
    assert "navigator.storage.persist" in patched
    assert "navigator.storage.persisted" in patched
    assert "STORAGE RISK" in patched
    assert "offline queue: storage risk" in patched
    assert "protected storage" in patched

    second = subprocess.run(
        [sys.executable, "voice-agent/scripts/patch_offline_storage_risk.py"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "already applied" in second.stdout
    assert (target / "app.py").read_text(encoding="utf-8") == patched
