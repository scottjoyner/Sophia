from pathlib import Path
import shutil
import subprocess
import sys


def test_capture_idempotency_patch_is_idempotent(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    src = repo / "voice-agent" / "src" / "voice_agent" / "server" / "app.py"
    script = repo / "voice-agent" / "scripts" / "patch_capture_idempotency.py"

    work = tmp_path / "work"
    target = work / "voice-agent" / "src" / "voice_agent" / "server"
    target.mkdir(parents=True)
    shutil.copy2(src, target / "app.py")

    scripts = work / "voice-agent" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(script, scripts / "patch_capture_idempotency.py")

    subprocess.run(
        [sys.executable, "voice-agent/scripts/patch_capture_idempotency.py"],
        cwd=work,
        check=True,
    )
    patched = (target / "app.py").read_text(encoding="utf-8")

    assert "from .capture_idempotency import CaptureIdempotencyStore" in patched
    assert "capture_idempotency = CaptureIdempotencyStore" in patched
    assert "app.state.capture_idempotency = capture_idempotency" in patched
    assert "client_capture_id: str = Form" in patched
    assert "client_capture_id_clean = CaptureIdempotencyStore.normalize_key(client_capture_id)" in patched
    assert "capture_idempotency.get(client_capture_id_clean)" in patched
    assert "mobile_capture_idempotent_replay" in patched
    assert "capture_idempotency.put(client_capture_id_clean, capture_id, response_payload)" in patched
    assert "idempotent_replay" not in patched or "CaptureIdempotencyStore" in patched

    second = subprocess.run(
        [sys.executable, "voice-agent/scripts/patch_capture_idempotency.py"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "already applied" in second.stdout
    assert (target / "app.py").read_text(encoding="utf-8") == patched
