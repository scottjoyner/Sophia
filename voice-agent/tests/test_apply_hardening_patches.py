import subprocess
import sys
from pathlib import Path


def test_apply_hardening_patches_dry_run_lists_order():
    repo = Path(__file__).resolve().parents[2]
    script = repo / "voice-agent" / "scripts" / "apply_hardening_patches.py"

    result = subprocess.run(
        [sys.executable, str(script), "--dry-run"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )

    out = result.stdout
    expected = [
        "patch_app_reliability.py",
        "patch_ui_session_refresh.py",
        "patch_request_hardening.py",
        "patch_rate_limits.py",
        "patch_offline_browser_queue.py",
        "patch_capture_idempotency.py",
    ]
    positions = [out.index(name) for name in expected]
    assert positions == sorted(positions)


def test_apply_hardening_patches_declares_patch_order():
    repo = Path(__file__).resolve().parents[2]
    content = (repo / "voice-agent" / "scripts" / "apply_hardening_patches.py").read_text()

    assert "PATCH_ORDER" in content
    assert "patch_app_reliability.py" in content
    assert "patch_capture_idempotency.py" in content
    assert content.index("patch_app_reliability.py") < content.index("patch_capture_idempotency.py")
