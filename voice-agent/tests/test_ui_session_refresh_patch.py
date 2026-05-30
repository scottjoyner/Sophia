from pathlib import Path
import runpy
import shutil
import subprocess
import sys


def test_ui_session_refresh_patch_is_idempotent(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    src = repo / "voice-agent" / "src" / "voice_agent" / "server" / "app.py"
    script = repo / "voice-agent" / "scripts" / "patch_ui_session_refresh.py"

    work = tmp_path / "work"
    target = work / "voice-agent" / "src" / "voice_agent" / "server"
    target.mkdir(parents=True)
    shutil.copy2(src, target / "app.py")

    scripts = work / "voice-agent" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(script, scripts / "patch_ui_session_refresh.py")

    subprocess.run(
        [sys.executable, "voice-agent/scripts/patch_ui_session_refresh.py"],
        cwd=work,
        check=True,
    )
    patched = (target / "app.py").read_text(encoding="utf-8")

    assert "sophia_auth_session_v1" in patched
    assert "function getAuthSession()" in patched
    assert "function setAuthSession(data)" in patched
    assert "function hydrateAuthFromCache()" in patched
    assert "function renderAuthSession(data, source = 'live')" in patched
    assert "function refreshTopStatus()" in patched
    assert "Forget trusted session" in patched
    assert "Using trusted voice session" in patched
    assert "setInterval(refreshTopStatus, 15000)" in patched
    assert "window.addEventListener('focus', refreshTopStatus)" in patched
    assert "if (mode === 'auto')" in patched
    assert "verifyVoice('manual')" in patched

    second = subprocess.run(
        [sys.executable, "voice-agent/scripts/patch_ui_session_refresh.py"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "already applied" in second.stdout
    assert (target / "app.py").read_text(encoding="utf-8") == patched
