#!/usr/bin/env python3
"""Wire Sophia reliability helpers into the large inline FastAPI app.

The capture UI and routes currently live in one large `app.py`.  To avoid risky
manual edits to that file, this script applies deterministic replacements that
connect the reliability modules added in this hardening pass:

- SQLite trusted session store
- `/session/status` and `/session/clear`
- stronger `/readyz`
- upload limits for verify, capture, voiceprint, and meeting endpoints
- local graph outbox for pending Neo4j memory writes
- `/graph/outbox/status` and `/graph/outbox/replay`

Run from repository root:

    python voice-agent/scripts/patch_app_reliability.py

The patcher is multi-phase and idempotent.  If an older version already applied
the trusted-session/upload/readiness phase, this script will still apply the
newer graph-outbox phase and upgrade graph outbox status to the richer summary
contract.
"""
from __future__ import annotations

from pathlib import Path

APP_PATH = Path("voice-agent/src/voice_agent/server/app.py")
CORE_PATCH_MARKER = "TrustedSessionStore"
OUTBOX_PATCH_MARKER = "GraphOutbox"
OUTBOX_SUMMARY_PATCH_MARKER = "graph_outbox.summary()"


class PatchError(RuntimeError):
    pass


def replace_once(content: str, old: str, new: str, label: str) -> str:
    count = content.count(old)
    if count != 1:
        raise PatchError(f"Expected exactly one match for {label!r}, found {count}.")
    return content.replace(old, new, 1)


def patch_core_reliability(content: str) -> str:
    if CORE_PATCH_MARKER in content:
        return content

    content = replace_once(
        content,
        """from .session_manager import SessionManager\n""",
        """from .session_manager import SessionManager\nfrom .readiness import build_readiness_report\nfrom .trusted_sessions import TrustedSessionStore\nfrom .upload_limits import (\n    CAPTURE_AUDIO_POLICY,\n    MEETING_AUDIO_POLICY,\n    VERIFY_AUDIO_POLICY,\n    VOICEPRINT_AUDIO_POLICY,\n    read_upload_with_limits,\n    safe_upload_suffix,\n    save_upload_with_limits,\n)\n""",
        "reliability imports",
    )

    content = replace_once(
        content,
        """    meeting_tasks = MeetingTaskManager()\n    app.state.config = config\n""",
        """    meeting_tasks = MeetingTaskManager()\n    trusted_sessions = TrustedSessionStore(Path(config.paths.artifacts_dir) / "trusted_sessions.sqlite")\n    app.state.config = config\n""",
        "trusted session store",
    )

    content = replace_once(
        content,
        """    app.state.meeting_tasks = meeting_tasks\n""",
        """    app.state.meeting_tasks = meeting_tasks\n    app.state.trusted_sessions = trusted_sessions\n""",
        "trusted session state",
    )

    content = replace_once(
        content,
        """    @app.get("/readyz")\n    async def readyz() -> Dict[str, Any]:\n        artifacts = Path(config.paths.artifacts_dir)\n        return {\n            "ok": artifacts.exists() and artifacts.is_dir(),\n            "protocol": config.server.protocol,\n            "artifacts_dir": str(artifacts),\n        }\n\n""",
        """    @app.get("/readyz")\n    async def readyz() -> Dict[str, Any]:\n        return build_readiness_report(config)\n\n""",
        "readyz route",
    )

    content = replace_once(
        content,
        """    @app.get("/memory-graph/status")\n    async def memory_graph_status() -> Dict[str, Any]:\n        return _neo4j_write_status()\n\n    @app.post("/auth/verify")\n""",
        """    @app.get("/memory-graph/status")\n    async def memory_graph_status() -> Dict[str, Any]:\n        return _neo4j_write_status()\n\n    @app.get("/session/status")\n    async def session_status(\n        user_id: str = "default",\n        session_id: str = "mobile",\n        device_id: str = "",\n        device_fingerprint: str = "",\n    ) -> Dict[str, Any]:\n        trusted_sessions.prune_expired()\n        session = trusted_sessions.get(\n            user_id=user_id,\n            session_id=session_id,\n            device_id=device_id,\n            device_fingerprint=device_fingerprint,\n        )\n        if not session:\n            return {"ok": True, "authenticated": False}\n        return {"ok": True, "authenticated": True, "session": session.to_dict()}\n\n    @app.post("/session/clear")\n    async def session_clear(\n        user_id: str = Form(default="default"),\n        session_id: str = Form(default="mobile"),\n        device_id: str = Form(default=""),\n        device_fingerprint: str = Form(default=""),\n    ) -> Dict[str, Any]:\n        cleared = trusted_sessions.clear(\n            user_id=user_id,\n            session_id=session_id,\n            device_id=device_id,\n            device_fingerprint=device_fingerprint,\n        )\n        return {"ok": True, "cleared": cleared}\n\n    @app.post("/auth/verify")\n""",
        "session routes",
    )

    content = replace_once(
        content,
        """    async def auth_verify(\n        audio: UploadFile = File(...),\n        user_id: str = Form(default="default"),\n        session_id: str = Form(default="mobile"),\n    ) -> Dict[str, Any]:\n        src_path = await _save_upload_for_audio_processing(config, audio, "verify")\n        wav_path = src_path\n""",
        """    async def auth_verify(\n        audio: UploadFile = File(...),\n        user_id: str = Form(default="default"),\n        session_id: str = Form(default="mobile"),\n        device_id: str = Form(default=""),\n        device_fingerprint: str = Form(default=""),\n    ) -> Dict[str, Any]:\n        src_path = await save_upload_with_limits(\n            audio,\n            Path(config.paths.artifacts_dir) / "tmp",\n            "verify",\n            VERIFY_AUDIO_POLICY,\n            default_suffix=".webm",\n        )\n        wav_path = src_path\n""",
        "auth verify signature and upload guard",
    )

    content = replace_once(
        content,
        """            payload = verify_audio_segment(config, session_id, user_id, samples, sr)\n            payload["source"] = "ui_auto_verify"\n            payload["neo4j_logged"] = _log_voice_ui_event_to_neo4j("voice_auth_verified", payload)\n""",
        """            payload = verify_audio_segment(config, session_id, user_id, samples, sr)\n            payload["source"] = "ui_auto_verify"\n            payload["device_id"] = device_id or payload.get("device_id")\n            payload["device_fingerprint"] = device_fingerprint\n            if payload.get("accepted"):\n                session = trusted_sessions.upsert(\n                    user_id=user_id,\n                    session_id=session_id,\n                    device_id=device_id,\n                    device_fingerprint=device_fingerprint,\n                    score=float(payload.get("score") or 0),\n                    accepted=True,\n                    match_source=str(payload.get("match_source") or ""),\n                    voiceprint_version_id=str(payload.get("voiceprint_version_id") or ""),\n                )\n                payload["trusted_session"] = session.to_dict()\n            payload["neo4j_logged"] = _log_voice_ui_event_to_neo4j("voice_auth_verified", payload)\n""",
        "auth verify trusted session write",
    )

    content = replace_once(content, """            suffix = _safe_upload_suffix(audio, default=".webm")\n""", """            suffix = safe_upload_suffix(audio, default=".webm", policy=CAPTURE_AUDIO_POLICY)\n""", "capture suffix guard")
    content = replace_once(content, """            data = await audio.read()\n""", """            data = await read_upload_with_limits(audio, CAPTURE_AUDIO_POLICY)\n""", "capture size guard")
    content = replace_once(content, """        suffix = _safe_upload_suffix(audio, default=".webm")\n""", """        suffix = safe_upload_suffix(audio, default=".webm", policy=VOICEPRINT_AUDIO_POLICY)\n""", "voiceprint enroll suffix guard")
    content = replace_once(content, """        src_path.write_bytes(await audio.read())\n""", """        src_path.write_bytes(await read_upload_with_limits(audio, VOICEPRINT_AUDIO_POLICY))\n""", "voiceprint enroll size guard")
    content = replace_once(
        content,
        """        suffix = _safe_upload_suffix(audio, default=".wav")\n        audio_file = override_dir / f"{capture_id}{suffix}"\n        data = await audio.read()\n""",
        """        suffix = safe_upload_suffix(audio, default=".wav", policy=VOICEPRINT_AUDIO_POLICY)\n        audio_file = override_dir / f"{capture_id}{suffix}"\n        data = await read_upload_with_limits(audio, VOICEPRINT_AUDIO_POLICY)\n""",
        "owner override upload guard",
    )
    content = replace_once(content, """        data = await audio.read()\n        asyncio.create_task(\n""", """        data = await read_upload_with_limits(audio, MEETING_AUDIO_POLICY)\n        asyncio.create_task(\n""", "meeting upload guard")

    return content


def patch_graph_outbox(content: str) -> str:
    if OUTBOX_PATCH_MARKER in content:
        return content

    content = replace_once(content, """from .readiness import build_readiness_report\n""", """from .graph_outbox import GraphOutbox, replay_graph_outbox_items\nfrom .readiness import build_readiness_report\n""", "graph outbox imports")
    content = replace_once(content, """    trusted_sessions = TrustedSessionStore(Path(config.paths.artifacts_dir) / "trusted_sessions.sqlite")\n    app.state.config = config\n""", """    trusted_sessions = TrustedSessionStore(Path(config.paths.artifacts_dir) / "trusted_sessions.sqlite")\n    graph_outbox = GraphOutbox(Path(config.paths.artifacts_dir) / "graph_outbox.sqlite")\n    app.state.config = config\n""", "graph outbox store")
    content = replace_once(content, """    app.state.trusted_sessions = trusted_sessions\n""", """    app.state.trusted_sessions = trusted_sessions\n    app.state.graph_outbox = graph_outbox\n""", "graph outbox app state")

    content = replace_once(
        content,
        """    @app.get("/session/status")\n""",
        """    @app.get("/graph/outbox/status")\n    async def graph_outbox_status() -> Dict[str, Any]:\n        return {"ok": True, **graph_outbox.summary()}\n\n    @app.post("/graph/outbox/replay")\n    async def graph_outbox_replay(limit: int = 25) -> Dict[str, Any]:\n        result = replay_graph_outbox_items(\n            graph_outbox,\n            neo4j_uri=config.neo4j.uri,\n            neo4j_user=config.neo4j.user,\n            neo4j_password=config.neo4j.password,\n            neo4j_database=config.neo4j.database,\n            limit=max(1, min(limit, 100)),\n        )\n        bus.publish("graph_outbox_replay", result)\n        return result\n\n    @app.get("/session/status")\n""",
        "graph outbox routes",
    )

    content = replace_once(
        content,
        """        graph_saved = False\n        graph_error = None\n        if config.neo4j.password:\n""",
        """        graph_saved = False\n        graph_pending = False\n        graph_outbox_id = None\n        graph_error = None\n        graph_write_payload = {\n            "user_id": user_id,\n            "capture_id": capture_id,\n            "transcript": transcript_text,\n            "audio_path": audio_path,\n            "content_type": content_type,\n            "duration_ms": duration_ms,\n            "metadata": {"session_id": session_id, "bytes": byte_count},\n            "context": context,\n        }\n        if config.neo4j.password:\n""",
        "capture graph pending state",
    )

    content = replace_once(
        content,
        """                save_capture_to_neo4j(\n                    config.neo4j.uri,\n                    config.neo4j.user,\n                    config.neo4j.password,\n                    user_id=user_id,\n                    capture_id=capture_id,\n                    transcript=transcript_text,\n                    audio_path=audio_path,\n                    content_type=content_type,\n                    database=config.neo4j.database,\n                    duration_ms=duration_ms,\n                    metadata={"session_id": session_id, "bytes": byte_count},\n                    context=context,\n                )\n""",
        """                save_capture_to_neo4j(\n                    config.neo4j.uri,\n                    config.neo4j.user,\n                    config.neo4j.password,\n                    user_id=graph_write_payload["user_id"],\n                    capture_id=graph_write_payload["capture_id"],\n                    transcript=graph_write_payload["transcript"],\n                    audio_path=graph_write_payload["audio_path"],\n                    content_type=graph_write_payload["content_type"],\n                    database=config.neo4j.database,\n                    duration_ms=graph_write_payload["duration_ms"],\n                    metadata=graph_write_payload["metadata"],\n                    context=graph_write_payload["context"],\n                )\n""",
        "capture graph write payload use",
    )

    content = replace_once(
        content,
        """            except RuntimeError as exc:\n                graph_error = str(exc)\n            except Exception as exc:\n                graph_error = f"{type(exc).__name__}: {exc}"\n""",
        """            except RuntimeError as exc:\n                graph_error = str(exc)\n            except Exception as exc:\n                graph_error = f"{type(exc).__name__}: {exc}"\n            if graph_error:\n                outbox_item = graph_outbox.enqueue(\n                    kind="capture",\n                    idempotency_key=f"capture:{capture_id}",\n                    payload=graph_write_payload,\n                    error=graph_error,\n                )\n                graph_pending = True\n                graph_outbox_id = outbox_item.id\n        else:\n            outbox_item = graph_outbox.enqueue(\n                kind="capture",\n                idempotency_key=f"capture:{capture_id}",\n                payload=graph_write_payload,\n                error="Neo4j password not configured",\n            )\n            graph_pending = True\n            graph_outbox_id = outbox_item.id\n            graph_error = "Neo4j password not configured"\n""",
        "capture graph outbox enqueue",
    )

    content = replace_once(content, """            "graph_saved": graph_saved,\n            "graph_error": graph_error,\n""", """            "graph_saved": graph_saved,\n            "graph_pending": graph_pending,\n            "graph_outbox_id": graph_outbox_id,\n            "graph_error": graph_error,\n""", "capture graph pending payload fields")

    return content


def patch_graph_outbox_summary(content: str) -> str:
    if OUTBOX_SUMMARY_PATCH_MARKER in content:
        return content
    if "GraphOutbox" not in content:
        return content
    return replace_once(
        content,
        """        return {"ok": True, "counts": graph_outbox.counts()}\n""",
        """        return {"ok": True, **graph_outbox.summary()}\n""",
        "graph outbox summary status route",
    )


def patch_content(content: str) -> str:
    content = patch_core_reliability(content)
    content = patch_graph_outbox(content)
    content = patch_graph_outbox_summary(content)
    return content


def main() -> int:
    if not APP_PATH.exists():
        raise SystemExit(f"Could not find {APP_PATH}; run from the repository root.")
    original = APP_PATH.read_text(encoding="utf-8")
    patched = patch_content(original)
    if patched == original:
        print("Sophia app reliability patch already applied.")
        return 0
    APP_PATH.write_text(patched, encoding="utf-8")
    print(f"Patched {APP_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
