#!/usr/bin/env python3
"""Patch capture lookup endpoint to reconcile from Neo4j.

After graph outbox replay succeeds, the browser queue may still have a local
record marked `server_graph_pending`. This patch upgrades
`GET /capture/by-client-id/{client_capture_id}` to check the idempotency cache
first and then Neo4j for completed graph memory.
"""
from __future__ import annotations

from pathlib import Path

APP_PATH = Path("voice-agent/src/voice_agent/server/app.py")
PATCH_MARKER = "lookup_capture_by_client_capture_id"


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
    if '@app.get("/capture/by-client-id/{client_capture_id}")' not in content:
        raise PatchError("Capture idempotency lookup route must exist before graph reconciliation patch.")

    content = replace_once(
        content,
        """from ..auth.neo4j_ingest import save_capture_to_neo4j, save_meeting_to_neo4j\n""",
        """from ..auth.neo4j_ingest import lookup_capture_by_client_capture_id, save_capture_to_neo4j, save_meeting_to_neo4j\n""",
        "neo4j lookup import",
    )

    content = replace_once(
        content,
        """    @app.get("/capture/by-client-id/{client_capture_id}")\n    async def capture_by_client_id(client_capture_id: str) -> Dict[str, Any]:\n        client_capture_id_clean = CaptureIdempotencyStore.normalize_key(client_capture_id)\n        if not client_capture_id_clean:\n            raise HTTPException(status_code=400, detail="client_capture_id is required")\n        replay = capture_idempotency.get(client_capture_id_clean)\n        if not replay:\n            return {"ok": True, "found": False, "client_capture_id": client_capture_id_clean}\n        return {"ok": True, "found": True, "client_capture_id": client_capture_id_clean, "capture": replay}\n\n""",
        """    @app.get("/capture/by-client-id/{client_capture_id}")\n    async def capture_by_client_id(client_capture_id: str) -> Dict[str, Any]:\n        client_capture_id_clean = CaptureIdempotencyStore.normalize_key(client_capture_id)\n        if not client_capture_id_clean:\n            raise HTTPException(status_code=400, detail="client_capture_id is required")\n        replay = capture_idempotency.get(client_capture_id_clean)\n        if replay and replay.get("graph_saved"):\n            return {"ok": True, "found": True, "source": "idempotency_cache", "client_capture_id": client_capture_id_clean, "capture": replay}\n        graph_lookup = lookup_capture_by_client_capture_id(\n            config.neo4j.uri,\n            config.neo4j.user,\n            config.neo4j.password or "",\n            client_capture_id=client_capture_id_clean,\n            database=config.neo4j.database,\n        )\n        if graph_lookup.get("found"):\n            capture_idempotency.put(client_capture_id_clean, str(graph_lookup.get("capture_id") or ""), graph_lookup)\n            return {"ok": True, "found": True, "source": "neo4j", "client_capture_id": client_capture_id_clean, "capture": graph_lookup}\n        if replay:\n            return {"ok": True, "found": True, "source": "idempotency_cache", "client_capture_id": client_capture_id_clean, "capture": replay}\n        return {"ok": True, "found": False, "client_capture_id": client_capture_id_clean}\n\n""",
        "capture lookup graph reconciliation",
    )
    return content


def main() -> int:
    if not APP_PATH.exists():
        raise SystemExit(f"Could not find {APP_PATH}; run from the repository root.")
    original = APP_PATH.read_text(encoding="utf-8")
    patched = patch_content(original)
    if patched == original:
        print("Sophia graph capture reconciliation patch already applied.")
        return 0
    APP_PATH.write_text(patched, encoding="utf-8")
    print(f"Patched {APP_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
