#!/usr/bin/env python3
"""Apply Sophia app hardening patchers in the recommended order.

The current app keeps a large inline FastAPI UI in app.py. Until that UI is
split into templates/static assets, these patchers apply deterministic updates
safely and idempotently.

Run from repository root:

    python voice-agent/scripts/apply_hardening_patches.py

Optional:

    python voice-agent/scripts/apply_hardening_patches.py --dry-run
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PATCH_ORDER = [
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
    parser = argparse.ArgumentParser(description="Apply Sophia hardening patchers in a safe order.")
    parser.add_argument("--dry-run", action="store_true", help="Print patch order without executing scripts.")
    parser.add_argument("--continue-on-error", action="store_true", help="Run all patchers even if one fails.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(__file__).resolve().parents[2]
    scripts_dir = repo / "voice-agent" / "scripts"

    print("Sophia hardening patch order:")
    for name in PATCH_ORDER:
        print(f"  - {name}")

    if args.dry_run:
        return 0

    failures: list[tuple[str, int]] = []
    for name in PATCH_ORDER:
        script = scripts_dir / name
        if not script.exists():
            print(f"ERROR: missing patcher: {script}", file=sys.stderr)
            failures.append((name, 127))
            if not args.continue_on_error:
                return 127
            continue
        print(f"\n==> Running {name}")
        result = subprocess.run([sys.executable, str(script)], cwd=repo)
        if result.returncode != 0:
            print(f"ERROR: {name} exited with {result.returncode}", file=sys.stderr)
            failures.append((name, result.returncode))
            if not args.continue_on_error:
                return result.returncode

    if failures:
        print("\nHardening patch failures:", file=sys.stderr)
        for name, code in failures:
            print(f"  - {name}: exit {code}", file=sys.stderr)
        return 1

    print("\nSophia hardening patchers completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
