#!/usr/bin/env python3
"""Patch Sophia FastAPI app with sensitive-route rate limiting.

Protects routes like auth verification, enrollment, meeting processing, graph
outbox replay, and AssistX dispatch from accidental loops or basic abuse.

Run from repository root:

    python voice-agent/scripts/patch_rate_limits.py
"""
from __future__ import annotations

from pathlib import Path

APP_PATH = Path("voice-agent/src/voice_agent/server/app.py")
PATCH_MARKER = "install_rate_limiter(app)"


class PatchError(RuntimeError):
    pass


def replace_once(content: str, old: str, new: str, label: str) -> str:
    count = content.count(old)
    if count != 1:
        raise PatchError(f"Expected exactly one match for {label!r}, found {count}.")
    return content.replace(old, new, 1)


def patch_content(content: str) -> str:
    if PATCH_MARKER in content:
        return content

    content = replace_once(
        content,
        """from .protocols import build_protocol_adapter\nfrom .session_manager import SessionManager\n""",
        """from .protocols import build_protocol_adapter\nfrom .rate_limits import install_rate_limiter\nfrom .session_manager import SessionManager\n""",
        "rate limiter import",
    )

    if "install_request_hardening(app)" in content:
        content = replace_once(
            content,
            """    install_request_hardening(app)\n    bus = EventBus()\n""",
            """    install_request_hardening(app)\n    install_rate_limiter(app)\n    bus = EventBus()\n""",
            "rate limiter install after request hardening",
        )
    else:
        content = replace_once(
            content,
            """def create_app(config: AppConfig) -> FastAPI:\n    app = FastAPI(title=\"Sophia Voice Agent\", version=\"0.1.0\")\n    bus = EventBus()\n""",
            """def create_app(config: AppConfig) -> FastAPI:\n    app = FastAPI(title=\"Sophia Voice Agent\", version=\"0.1.0\")\n    install_rate_limiter(app)\n    bus = EventBus()\n""",
            "rate limiter install",
        )

    return content


def main() -> int:
    if not APP_PATH.exists():
        raise SystemExit(f"Could not find {APP_PATH}; run from the repository root.")
    original = APP_PATH.read_text(encoding="utf-8")
    patched = patch_content(original)
    if patched == original:
        print("Sophia rate limit patch already applied.")
        return 0
    APP_PATH.write_text(patched, encoding="utf-8")
    print(f"Patched {APP_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
