from pathlib import Path
import shutil
import subprocess
import sys


def _copy_patch_script(repo: Path, work: Path, name: str) -> None:
    scripts = work / "voice-agent" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    shutil.copy2(repo / "voice-agent" / "scripts" / name, scripts / name)


def test_capture_idempotency_patch_is_idempotent(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    src = repo / "voice-agent" / "src" / "voice_agent" / "server" / "app.py"
    script = repo / "voice-agent" / "scripts" / "patch_capture_idempotency.py"

    work = tmp_path / "work"
    target = work / "voice-agent" / "src" / "voice_agent" / "server"
    target.mkdir(parents=True)
    shutil.copy2(src, target / "app.py")

    _copy_patch_script(repo, work, "patch_capture_idempotency.py")

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

    second = subprocess.run(
        [sys.executable, "voice-agent/scripts/patch_capture_idempotency.py"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "already applied" in second.stdout
    assert (target / "app.py").read_text(encoding="utf-8") == patched


def test_capture_idempotency_patch_after_graph_outbox_patch(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    src = repo / "voice-agent" / "src" / "voice_agent" / "server" / "app.py"

    work = tmp_path / "work_outbox_first"
    target = work / "voice-agent" / "src" / "voice_agent" / "server"
    target.mkdir(parents=True)
    shutil.copy2(src, target / "app.py")

    _copy_patch_script(repo, work, "patch_app_reliability.py")
    _copy_patch_script(repo, work, "patch_capture_idempotency.py")

    subprocess.run(
        [sys.executable, "voice-agent/scripts/patch_app_reliability.py"],
        cwd=work,
        check=True,
    )
    subprocess.run(
        [sys.executable, "voice-agent/scripts/patch_capture_idempotency.py"],
        cwd=work,
        check=True,
    )
    patched = (target / "app.py").read_text(encoding="utf-8")

    assert "graph_write_payload" in patched
    assert '"client_capture_id": client_capture_id_clean or None' in patched
    assert "capture_idempotency = CaptureIdempotencyStore" in patched
    assert "capture_idempotency.get(client_capture_id_clean)" in patched
    assert "capture_idempotency.put(client_capture_id_clean, capture_id, response_payload)" in patched
