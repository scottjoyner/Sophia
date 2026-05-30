#!/usr/bin/env python3
"""Patch Sophia capture route with client_capture_id idempotency.

This protects offline browser queue retries from creating duplicate server
captures or duplicate Neo4j memory writes after uncertain network failures.

Run from repository root:

    python voice-agent/scripts/patch_capture_idempotency.py

The patcher is intentionally order-tolerant with the reliability/offline UI
patchers. It can run before or after those patches as long as app.py still uses
the current create_app/capture route structure.
"""
from __future__ import annotations

from pathlib import Path

APP_PATH = Path("voice-agent/src/voice_agent/server/app.py")
PATCH_MARKER = "CaptureIdempotencyStore"
CLIENT_ID_MARKER = "client_capture_id: str = Form"
LOOKUP_ROUTE_MARKER = '@app.get("/capture/by-client-id/{client_capture_id}")'


class PatchError(RuntimeError):
    pass


def replace_once(content: str, old: str, new: str, label: str) -> str:
    count = content.count(old)
    if count != 1:
        raise PatchError(f"Expected exactly one match for {label!r}, found {count}.")
    return content.replace(old, new, 1)


def insert_after_once(content: str, needle: str, insertion: str, marker: str, label: str) -> str:
    if marker in content:
        return content
    return replace_once(content, needle, needle + insertion, label)


def ensure_client_capture_id(content: str) -> str:
    if CLIENT_ID_MARKER not in content:
        content = replace_once(
            content,
            """        activity_context: str = Form(default=""),\n    ) -> Dict[str, Any]:\n""",
            """        activity_context: str = Form(default=""),\n        client_capture_id: str = Form(default=""),\n    ) -> Dict[str, Any]:\n""",
            "capture client id form field",
        )

    if "client_capture_id_clean or None" not in content:
        if "\"client_capture_id\": client_capture_id_clean or None" not in content:
            content = replace_once(
                content,
                """            "capture_id": capture_id,\n""",
                """            "capture_id": capture_id,\n            "client_capture_id": client_capture_id_clean or None,\n""",
                "capture client id response",
            )

    # Update whichever graph-write metadata shape is present.
    if "\"client_capture_id\": client_capture_id_clean or None" not in content:
        if "metadata={\"session_id\": session_id, \"bytes\": byte_count}" in content:
            content = replace_once(
                content,
                """                    metadata={"session_id": session_id, "bytes": byte_count},\n""",
                """                    metadata={"session_id": session_id, "bytes": byte_count, "client_capture_id": client_capture_id_clean or None},\n""",
                "capture client id direct metadata",
            )
        elif "\"metadata\": {\"session_id\": session_id, \"bytes\": byte_count}" in content:
            content = replace_once(
                content,
                """            "metadata": {"session_id": session_id, "bytes": byte_count},\n""",
                """            "metadata": {"session_id": session_id, "bytes": byte_count, "client_capture_id": client_capture_id_clean or None},\n""",
                "capture client id graph outbox metadata",
            )
    return content


def patch_base_idempotency(content: str) -> str:
    if PATCH_MARKER in content:
        return content

    content = ensure_client_capture_id(content)

    content = replace_once(
        content,
        """from .session_manager import SessionManager\n""",
        """from .capture_idempotency import CaptureIdempotencyStore\nfrom .session_manager import SessionManager\n""",
        "capture idempotency import",
    )

    content = insert_after_once(
        content,
        """    meeting_tasks = MeetingTaskManager()\n""",
        """    capture_idempotency = CaptureIdempotencyStore(Path(config.paths.artifacts_dir) / "capture_idempotency.sqlite")\n""",
        "capture_idempotency = CaptureIdempotencyStore",
        "capture idempotency store",
    )

    content = insert_after_once(
        content,
        """    app.state.meeting_tasks = meeting_tasks\n""",
        """    app.state.capture_idempotency = capture_idempotency\n""",
        "app.state.capture_idempotency = capture_idempotency",
        "capture idempotency app state",
    )

    content = replace_once(
        content,
        """        capture_id = uuid.uuid4().hex\n""",
        """        capture_id = uuid.uuid4().hex\n        client_capture_id_clean = CaptureIdempotencyStore.normalize_key(client_capture_id)\n        if client_capture_id_clean:\n            replay = capture_idempotency.get(client_capture_id_clean)\n            if replay:\n                bus.publish("mobile_capture_idempotent_replay", replay)\n                return replay\n""",
        "capture idempotency early replay",
    )

    # If another patcher already inserted a simple client_capture_id_clean assignment, remove duplicate.
    content = content.replace(
        """        client_capture_id_clean = client_capture_id.strip()[:128]\n""",
        """""",
        1,
    )

    if "response_payload = {\"ok\": True, **payload}" in content:
        # Already has another response-payload refactor; add cache write if missing.
        content = insert_after_once(
            content,
            """        response_payload = {"ok": True, **payload}\n""",
            """        if client_capture_id_clean:\n            capture_idempotency.put(client_capture_id_clean, capture_id, response_payload)\n""",
            "capture_idempotency.put(client_capture_id_clean, capture_id, response_payload)",
            "capture idempotency cache existing response payload",
        )
    else:
        content = replace_once(
            content,
            """        bus.publish("mobile_capture_saved", payload)\n        return {"ok": True, **payload}\n""",
            """        response_payload = {"ok": True, **payload}\n        if client_capture_id_clean:\n            capture_idempotency.put(client_capture_id_clean, capture_id, response_payload)\n        bus.publish("mobile_capture_saved", payload)\n        return response_payload\n""",
            "capture idempotency store response",
        )

    return content


def patch_lookup_route(content: str) -> str:
    if LOOKUP_ROUTE_MARKER in content:
        return content
    if PATCH_MARKER not in content:
        return content
    route = """
    @app.get("/capture/by-client-id/{client_capture_id}")
    async def capture_by_client_id(client_capture_id: str) -> Dict[str, Any]:
        client_capture_id_clean = CaptureIdempotencyStore.normalize_key(client_capture_id)
        if not client_capture_id_clean:
            raise HTTPException(status_code=400, detail="client_capture_id is required")
        replay = capture_idempotency.get(client_capture_id_clean)
        if not replay:
            return {"ok": True, "found": False, "client_capture_id": client_capture_id_clean}
        return {"ok": True, "found": True, "client_capture_id": client_capture_id_clean, "capture": replay}

"""
    return replace_once(
        content,
        """    @app.post("/capture")\n""",
        route + "    @app.post(\"/capture\")\n",
        "capture idempotency lookup route",
    )


def patch_content(content: str) -> str:
    content = patch_base_idempotency(content)
    content = patch_lookup_route(content)
    return content


def main() -> int:
    if not APP_PATH.exists():
        raise SystemExit(f"Could not find {APP_PATH}; run from the repository root.")
    original = APP_PATH.read_text(encoding="utf-8")
    patched = patch_content(original)
    if patched == original:
        print("Sophia capture idempotency patch already applied.")
        return 0
    APP_PATH.write_text(patched, encoding="utf-8")
    print(f"Patched {APP_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
