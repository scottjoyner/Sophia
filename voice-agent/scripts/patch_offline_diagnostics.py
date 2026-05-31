#!/usr/bin/env python3
"""Patch Sophia with an offline reliability diagnostics endpoint.

Requires the reliability and capture idempotency patchers. Adds:

    GET /diagnostics/offline

The endpoint summarizes readiness, graph outbox status, capture idempotency cache
health, and architecture roles for operator/UI troubleshooting.
"""
from __future__ import annotations

from pathlib import Path

APP_PATH = Path("voice-agent/src/voice_agent/server/app.py")
PATCH_MARKER = '@app.get("/diagnostics/offline")'


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
    if "graph_outbox.summary()" not in content:
        raise PatchError("Graph outbox reliability patch must be applied before offline diagnostics.")
    if "capture_idempotency.summary()" not in content and "CaptureIdempotencyStore" not in content:
        raise PatchError("Capture idempotency patch must be applied before offline diagnostics.")

    route = """
    @app.get("/diagnostics/offline")
    async def offline_diagnostics() -> Dict[str, Any]:
        idempotency_pruned = 0
        if hasattr(capture_idempotency, "prune_expired"):
            idempotency_pruned = capture_idempotency.prune_expired()
        return {
            "ok": True,
            "readiness": build_readiness_report(config),
            "graph_outbox": graph_outbox.summary(),
            "capture_idempotency": capture_idempotency.summary(),
            "idempotency_pruned": idempotency_pruned,
            "roles": {
                "browser_indexeddb": "temporary offline capture queue",
                "server_sqlite_idempotency": "temporary retry response cache",
                "server_sqlite_graph_outbox": "temporary Neo4j write retry journal",
                "filesystem": "accepted raw audio artifacts",
                "neo4j": "durable Sophia memory brain",
            },
        }

"""
    return replace_once(
        content,
        """    @app.get("/capture/by-client-id/{client_capture_id}")\n""",
        route + "    @app.get(\"/capture/by-client-id/{client_capture_id}\")\n",
        "offline diagnostics route",
    )


def main() -> int:
    if not APP_PATH.exists():
        raise SystemExit(f"Could not find {APP_PATH}; run from the repository root.")
    original = APP_PATH.read_text(encoding="utf-8")
    patched = patch_content(original)
    if patched == original:
        print("Sophia offline diagnostics patch already applied.")
        return 0
    APP_PATH.write_text(patched, encoding="utf-8")
    print(f"Patched {APP_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
