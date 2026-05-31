from pathlib import Path
import shutil
import subprocess
import sys


def _copy_patch_script(repo: Path, work: Path, name: str) -> None:
    scripts = work / "voice-agent" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    shutil.copy2(repo / "voice-agent" / "scripts" / name, scripts / name)


def test_offline_diagnostics_patch_after_dependencies(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    src = repo / "voice-agent" / "src" / "voice_agent" / "server" / "app.py"

    work = tmp_path / "work"
    target = work / "voice-agent" / "src" / "voice_agent" / "server"
    target.mkdir(parents=True)
    shutil.copy2(src, target / "app.py")

    for name in [
        "patch_app_reliability.py",
        "patch_capture_idempotency.py",
        "patch_offline_diagnostics.py",
    ]:
        _copy_patch_script(repo, work, name)

    subprocess.run([sys.executable, "voice-agent/scripts/patch_app_reliability.py"], cwd=work, check=True)
    subprocess.run([sys.executable, "voice-agent/scripts/patch_capture_idempotency.py"], cwd=work, check=True)
    subprocess.run([sys.executable, "voice-agent/scripts/patch_offline_diagnostics.py"], cwd=work, check=True)

    patched = (target / "app.py").read_text(encoding="utf-8")
    assert '@app.get("/diagnostics/offline")' in patched
    assert "async def offline_diagnostics()" in patched
    assert "build_readiness_report(config)" in patched
    assert "graph_outbox.summary()" in patched
    assert "capture_idempotency.summary()" in patched
    assert "browser_indexeddb" in patched
    assert "durable Sophia memory brain" in patched

    second = subprocess.run(
        [sys.executable, "voice-agent/scripts/patch_offline_diagnostics.py"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "already applied" in second.stdout
    assert (target / "app.py").read_text(encoding="utf-8") == patched
