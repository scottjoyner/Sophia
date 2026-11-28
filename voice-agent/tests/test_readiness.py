from __future__ import annotations

import sqlite3
from pathlib import Path

from voice_agent.config import AppConfig
from voice_agent.server import readiness
from voice_agent.server.readiness import (
    VALID_PROTOCOLS,
    _check_sqlite,
    _check_writable_dir,
    build_readiness_report,
)


def _config(tmp_path: Path) -> AppConfig:
    """Config whose artifacts live under a tmp_path, with no external services."""
    config = AppConfig()
    config.paths = config.paths.model_copy(update={"artifacts_dir": str(tmp_path / "runs")})
    return config


def _stub_ffmpeg(monkeypatch, path: str | None = "/usr/bin/ffmpeg") -> None:
    """Keep the readiness probe hermetic: never discover a real ffmpeg binary."""
    monkeypatch.setattr(readiness.shutil, "which", lambda name: path)


def test_check_writable_dir_creates_nested_path_and_leaves_no_probe_file(tmp_path) -> None:
    target = tmp_path / "a" / "b" / "c"
    assert _check_writable_dir(target) == {"ok": True, "path": str(target), "writable": True}
    assert target.is_dir()
    assert list(target.iterdir()) == []


def test_check_writable_dir_reports_failure(tmp_path) -> None:
    (tmp_path / "blocked").write_text("a file, not a directory")
    result = _check_writable_dir(tmp_path / "blocked")
    assert result["ok"] is False
    assert result["writable"] is False
    assert result["path"] == str(tmp_path / "blocked")
    assert "FileExistsError" in result["error"]


def test_check_sqlite_creates_db_and_clears_stale_rows(tmp_path) -> None:
    path = tmp_path / "nested" / "probe.sqlite"
    assert _check_sqlite(path) == {"ok": True, "path": str(path), "openable": True}
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO readiness_probe (ts) VALUES ('stale')")
    _check_sqlite(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM readiness_probe").fetchone()[0] == 0


def test_check_sqlite_reports_failure_for_directory(tmp_path) -> None:
    path = tmp_path / "results.sqlite"
    path.mkdir()
    result = _check_sqlite(path)
    assert result["ok"] is False
    assert result["openable"] is False
    assert "Error" in result["error"]


def test_report_ok_when_all_required_checks_pass(tmp_path, monkeypatch) -> None:
    _stub_ffmpeg(monkeypatch)
    config = _config(tmp_path)
    config.neo4j = config.neo4j.model_copy(update={"password": "pw"})

    report = build_readiness_report(config)

    assert report["ok"] is True
    assert set(report["checks"]) == {
        "artifacts_dir",
        "capture_dir",
        "voiceprint_registry",
        "ffmpeg",
        "protocol",
        "memory_graph",
        "owner_override",
    }
    checks = report["checks"]
    for name in ("artifacts_dir", "capture_dir", "voiceprint_registry", "ffmpeg", "protocol"):
        assert checks[name]["ok"] is True
    assert checks["artifacts_dir"]["writable"] is True
    assert checks["voiceprint_registry"]["openable"] is True
    assert checks["ffmpeg"]["path"] == "/usr/bin/ffmpeg"
    assert checks["protocol"]["valid_values"] == ["hermes_overlay_v1", "native_ws"]
    assert checks["memory_graph"]["write_ready"] is True
    assert checks["memory_graph"]["reason"] is None
    assert checks["owner_override"]["ok"] is True
    assert checks["owner_override"]["reason"] is None
    # The probe creates the directories it checks, under tmp_path only.
    assert (tmp_path / "runs" / "captures").is_dir()
    assert (tmp_path / "runs" / "results.sqlite").is_file()


def test_report_defaults_capture_dir_under_artifacts_dir(tmp_path, monkeypatch) -> None:
    _stub_ffmpeg(monkeypatch)
    config = _config(tmp_path)
    assert config.paths.capture_dir is None

    report = build_readiness_report(config)

    assert report["checks"]["capture_dir"]["path"] == str(tmp_path / "runs" / "captures")
    assert report["checks"]["capture_dir"]["ok"] is True


def test_report_unwritable_artifacts_dir_is_required_failure(tmp_path, monkeypatch) -> None:
    _stub_ffmpeg(monkeypatch)
    (tmp_path / "runs").write_text("not a directory")

    report = build_readiness_report(_config(tmp_path))

    assert report["ok"] is False
    assert report["checks"]["artifacts_dir"]["ok"] is False
    assert report["checks"]["artifacts_dir"]["writable"] is False
    assert "FileExistsError" in report["checks"]["artifacts_dir"]["error"]


def test_report_unopenable_registry_db_is_required_failure(tmp_path, monkeypatch) -> None:
    _stub_ffmpeg(monkeypatch)
    (tmp_path / "runs" / "results.sqlite").mkdir(parents=True)

    report = build_readiness_report(_config(tmp_path))

    assert report["ok"] is False
    check = report["checks"]["voiceprint_registry"]
    assert check["ok"] is False
    assert check["openable"] is False
    assert "Error" in check["error"]


def test_report_invalid_protocol_is_required_failure(tmp_path, monkeypatch) -> None:
    _stub_ffmpeg(monkeypatch)
    assert VALID_PROTOCOLS == {"native_ws", "hermes_overlay_v1"}
    config = _config(tmp_path)
    config.server = config.server.model_copy(update={"protocol": "carrier_pigeon"})

    report = build_readiness_report(config)

    check = report["checks"]["protocol"]
    assert check["ok"] is False
    assert check["value"] == "carrier_pigeon"
    assert check["valid_values"] == ["hermes_overlay_v1", "native_ws"]
    assert report["ok"] is False


def test_report_missing_ffmpeg_is_required_failure(tmp_path, monkeypatch) -> None:
    _stub_ffmpeg(monkeypatch, path=None)

    report = build_readiness_report(_config(tmp_path))

    assert report["checks"]["ffmpeg"] == {"ok": False, "path": None}
    assert report["ok"] is False


def test_report_memory_graph_without_password_is_honest_but_not_required(tmp_path, monkeypatch) -> None:
    _stub_ffmpeg(monkeypatch)
    config = _config(tmp_path)
    config.neo4j = config.neo4j.model_copy(
        update={"uri": "bolt://graph:7687", "database": "neo4j", "password": None}
    )

    report = build_readiness_report(config)

    check = report["checks"]["memory_graph"]
    assert check["ok"] is True
    assert check["write_ready"] is False
    assert check["uri"] == "bolt://graph:7687"
    assert check["database"] == "neo4j"
    assert "Neo4j password" in check["reason"]
    # Local capture still works, so this must not fail overall readiness.
    assert report["ok"] is True


def test_report_owner_override_requires_token_when_enabled(tmp_path, monkeypatch) -> None:
    _stub_ffmpeg(monkeypatch)
    config = _config(tmp_path)
    config.auth = config.auth.model_copy(
        update={"owner_override_enabled": True, "owner_override_token": None}
    )

    report = build_readiness_report(config)

    check = report["checks"]["owner_override"]
    assert check["ok"] is False
    assert check["enabled"] is True
    assert check["key_configured"] is False
    assert "SOPHIA_OWNER_OVERRIDE_TOKEN" in check["reason"]
    # Advisory check: does not block readiness.
    assert report["ok"] is True

    config.auth = config.auth.model_copy(update={"owner_override_token": "key"})
    fixed = build_readiness_report(config)
    assert fixed["checks"]["owner_override"]["ok"] is True
    assert fixed["checks"]["owner_override"]["key_configured"] is True
    assert fixed["checks"]["owner_override"]["reason"] is None
