#!/usr/bin/env python3
"""Check the full Sophia hardening patch pipeline in a disposable workspace.

This script copies only the files needed to patch `app.py`, applies the full
hardening patch sequence to the copy, then verifies the patched result. It does
ot modify the developer working tree.

Run from repository root:

    python voice-agent/scripts/check_patch_pipeline.py
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PATCH_SCRIPTS = [
    "apply_hardening_patches.py",
    "verify_hardening.py",
    "patch_app_reliability.py",
    "patch_ui_session_refresh.py",
    "patch_request_hardening.py",
    "patch_rate_limits.py",
    "patch_offline_browser_queue.py",
    "patch_offline_storage_risk.py",
    "patch_capture_idempotency.py",
    "patch_graph_capture_reconciliation.py",
    "patch_offline_diagnostics.py",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Sophia hardening patchers against a temporary app.py copy.")
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    parser.add_argument("--keep-temp", action="store_true", help="Keep the temporary workspace for inspection.")
    return parser


def copy_patch_workspace(repo: Path, dest: Path) -> None:
    app_src = repo / "voice-agent" / "src" / "voice_agent" / "server" / "app.py"
    app_dest = dest / "voice-agent" / "src" / "voice_agent" / "server" / "app.py"
    app_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(app_src, app_dest)

    scripts_dest = dest / "voice-agent" / "scripts"
    scripts_dest.mkdir(parents=True, exist_ok=True)
    for name in PATCH_SCRIPTS:
        shutil.copy2(repo / "voice-agent" / "scripts" / name, scripts_dest / name)


def run_cmd(cmd: list[str], *, cwd: Path) -> dict:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return {
        "cmd": cmd,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(__file__).resolve().parents[2]
    temp_obj = tempfile.TemporaryDirectory(prefix="sophia-patch-pipeline-")
    temp = Path(temp_obj.name)
    if args.keep_temp:
        temp_obj.cleanup = lambda: None  # type: ignore[method-assign]

    copy_patch_workspace(repo, temp)
    apply_result = run_cmd([sys.executable, "voice-agent/scripts/apply_hardening_patches.py"], cwd=temp)
    verify_result = run_cmd([sys.executable, "voice-agent/scripts/verify_hardening.py", "--json"], cwd=temp)

    try:
        verify_payload = json.loads(verify_result["stdout"] or "{}")
    except json.JSONDecodeError:
        verify_payload = {"ok": False, "error": "verify_hardening.py did not emit JSON"}

    ok = apply_result["returncode"] == 0 and verify_result["returncode"] == 0 and bool(verify_payload.get("ok"))
    payload = {
        "ok": ok,
        "workspace": str(temp),
        "apply": apply_result,
        "verify": verify_result,
        "verify_payload": verify_payload,
    }

    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print("Sophia disposable patch pipeline check")
        print(f"  workspace: {temp}")
        print(f"  apply exit: {apply_result['returncode']}")
        print(f"  verify exit: {verify_result['returncode']}")
        print(f"  ok: {ok}")
        if not ok:
            print("\nApply stdout:\n" + apply_result["stdout"])
            print("\nApply stderr:\n" + apply_result["stderr"])
            print("\nVerify stdout:\n" + verify_result["stdout"])
            print("\nVerify stderr:\n" + verify_result["stderr"])

    if not args.keep_temp:
        temp_obj.cleanup()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
