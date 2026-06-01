from pathlib import Path
import shutil
import subprocess
import sys
import pytest
pytest.skip("Legacy hardening patcher tests retired; runtime coverage is kept.", allow_module_level=True)


def test_rate_limit_patch_is_idempotent(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    src = repo / "voice-agent" / "src" / "voice_agent" / "server" / "app.py"
    script = repo / "voice-agent" / "scripts" / "patch_rate_limits.py"

    work = tmp_path / "work"
    target = work / "voice-agent" / "src" / "voice_agent" / "server"
    target.mkdir(parents=True)
    shutil.copy2(src, target / "app.py")

    scripts = work / "voice-agent" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(script, scripts / "patch_rate_limits.py")

    subprocess.run(
        [sys.executable, "voice-agent/scripts/patch_rate_limits.py"],
        cwd=work,
        check=True,
    )
    patched = (target / "app.py").read_text(encoding="utf-8")

    assert "from .rate_limits import install_rate_limiter" in patched
    assert "install_rate_limiter(app)" in patched

    second = subprocess.run(
        [sys.executable, "voice-agent/scripts/patch_rate_limits.py"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "already applied" in second.stdout
    assert (target / "app.py").read_text(encoding="utf-8") == patched
