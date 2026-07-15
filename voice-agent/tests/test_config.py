from __future__ import annotations

from pathlib import Path

import pytest

from voice_agent.config import AppConfig, load_config


def test_load_config_defaults_without_env(monkeypatch) -> None:
    monkeypatch.delenv("NEO4J_URI", raising=False)
    monkeypatch.delenv("SOPHIA_LLM_PROVIDER", raising=False)
    config = load_config(None)
    assert isinstance(config, AppConfig)
    assert config.llm.provider == "mock"
    assert config.auth.owner_user_id == "scott"


def test_load_config_reads_neo4j_env(monkeypatch) -> None:
    monkeypatch.setenv("NEO4J_URI", "bolt://example:7687")
    monkeypatch.setenv("NEO4J_USER", "neo")
    monkeypatch.setenv("NEO4J_PASSWORD", "sekret")
    monkeypatch.setenv("NEO4J_DATABASE", "prod")
    config = load_config(None)
    assert config.neo4j.uri == "bolt://example:7687"
    assert config.neo4j.user == "neo"
    assert config.neo4j.password == "sekret"
    assert config.neo4j.database == "prod"


def test_load_config_reads_llm_and_auth_env(monkeypatch) -> None:
    monkeypatch.setenv("SOPHIA_LLM_PROVIDER", "openai")
    monkeypatch.setenv("SOPHIA_LLM_MODEL", "gpt-x")
    monkeypatch.setenv("SOPHIA_INTENT_PROVIDER", "hermes")
    monkeypatch.setenv("SOPHIA_OWNER_USER_ID", "bob")
    monkeypatch.setenv("SOPHIA_OWNER_OVERRIDE_ENABLED", "true")
    monkeypatch.setenv("SOPHIA_ADAPTIVE_THRESHOLD_MIN", "0.5")
    config = load_config(None)
    assert config.llm.provider == "openai"
    assert config.llm.model == "gpt-x"
    assert config.llm.intent_provider == "hermes"
    assert config.auth.owner_user_id == "bob"
    assert config.auth.owner_override_enabled is True
    assert config.auth.adaptive_threshold_min == 0.5


def test_load_config_from_yaml_file(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SOPHIA_LLM_PROVIDER", raising=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "llm:\n  provider: openai\n  model: custom-model\n"
        "auth:\n  threshold: 0.9\n"
        "neo4j:\n  password: fromfile\n"
    )
    config = load_config(str(cfg))
    assert config.llm.provider == "openai"
    assert config.llm.model == "custom-model"
    assert config.auth.threshold == 0.9
    assert config.neo4j.password == "fromfile"


def test_read_secret_file(tmp_path) -> None:
    from voice_agent.config import _read_secret_file

    secret = tmp_path / "secret.txt"
    secret.write_text("  topsecret  \n")
    assert _read_secret_file(str(secret)) == "topsecret"
    assert _read_secret_file(str(tmp_path / "missing.txt")) is None


def test_password_file_override(tmp_path, monkeypatch) -> None:
    secret = tmp_path / "neo4j_pass.txt"
    secret.write_text("filepassword")
    monkeypatch.setenv("NEO4J_PASSWORD_FILE", str(secret))
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    config = load_config(None)
    assert config.neo4j.password == "filepassword"


def test_capture_dir_env(tmp_path, monkeypatch) -> None:
    capture = tmp_path / "captures"
    monkeypatch.setenv("SOPHIA_CAPTURE_DIR", str(capture))
    config = load_config(None)
    assert config.paths.capture_dir == str(capture)
