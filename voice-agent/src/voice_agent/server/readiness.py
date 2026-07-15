from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from ..config import AppConfig

VALID_PROTOCOLS = {"native_ws", "hermes_overlay_v1"}


def _check_writable_dir(path: Path) -> dict[str, Any]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".sophia-ready-", delete=True) as fh:
            fh.write(b"ok")
            fh.flush()
        return {"ok": True, "path": str(path), "writable": True}
    except Exception as exc:
        return {"ok": False, "path": str(path), "writable": False, "error": f"{type(exc).__name__}: {exc}"}


def _check_sqlite(path: Path) -> dict[str, Any]:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS readiness_probe (id INTEGER PRIMARY KEY, ts TEXT)")
            conn.execute("DELETE FROM readiness_probe")
        return {"ok": True, "path": str(path), "openable": True}
    except Exception as exc:
        return {"ok": False, "path": str(path), "openable": False, "error": f"{type(exc).__name__}: {exc}"}


def build_readiness_report(config: AppConfig) -> dict[str, Any]:
    artifacts_dir = Path(config.paths.artifacts_dir)
    capture_dir = Path(config.paths.capture_dir or (artifacts_dir / "captures"))
    registry_db = artifacts_dir / "results.sqlite"

    checks = {
        "artifacts_dir": _check_writable_dir(artifacts_dir),
        "capture_dir": _check_writable_dir(capture_dir),
        "voiceprint_registry": _check_sqlite(registry_db),
        "ffmpeg": {"ok": bool(shutil.which("ffmpeg")), "path": shutil.which("ffmpeg")},
        "protocol": {
            "ok": config.server.protocol in VALID_PROTOCOLS,
            "value": config.server.protocol,
            "valid_values": sorted(VALID_PROTOCOLS),
        },
        "memory_graph": {
            "ok": True,
            "write_ready": bool(config.neo4j.password),
            "uri": config.neo4j.uri,
            "database": config.neo4j.database,
            "reason": None if config.neo4j.password else "Neo4j password not configured; local capture still works",
        },
        "owner_override": {
            "ok": (not config.auth.owner_override_enabled) or bool(config.auth.owner_override_token),
            "enabled": config.auth.owner_override_enabled,
            "key_configured": bool(config.auth.owner_override_token),
            "reason": None
            if (not config.auth.owner_override_enabled or config.auth.owner_override_token)
            else "Owner override enabled but SOPHIA_OWNER_OVERRIDE_TOKEN is empty",
        },
    }

    required = ["artifacts_dir", "capture_dir", "voiceprint_registry", "ffmpeg", "protocol"]
    ok = all(bool(checks[name].get("ok")) for name in required)
    return {"ok": ok, "checks": checks}
