from __future__ import annotations

from pathlib import Path

from voice_agent.config import AppConfig, load_config
from voice_agent.server.assistant import Assistant


_RETIRED_ENV_VARS = (
    "SOPHIA_FLEET_DISCOVERY",
    "SOPHIA_LOCAL_FLEET_DISCOVERY",
    "SOPHIA_FLEET_NODE_PORT",
    "SOPHIA_FLEET_ROUTER_URL",
    "SOPHIA_FLEET_CANDIDATE_NODES",
    "SOPHIA_FLEET_REFRESH_INTERVAL",
    "SOPHIA_FLEET_CHAT_MAX_PARAMS",
    "SOPHIA_FLEET_TASK_MIN_PARAMS",
)


def test_llm_config_has_no_physical_fleet_authority(monkeypatch) -> None:
    for name in _RETIRED_ENV_VARS:
        monkeypatch.setenv(name, "enabled-but-retired")

    config = load_config(None)

    for field in (
        "fleet_discovery",
        "local_fleet_discovery",
        "fleet_node_port",
        "fleet_router_url",
        "fleet_candidate_nodes",
        "fleet_refresh_interval",
        "fleet_chat_max_params",
        "fleet_task_min_params",
    ):
        assert not hasattr(config.llm, field)


def test_assistant_exposes_no_discovery_or_endpoint_ranking_methods() -> None:
    config = AppConfig()
    config.llm = config.llm.model_copy(
        update={
            "intent_provider": "openai",
            "intent_base_url": "http://auto-router:8088/v1",
            "intent_model": "auto/fast",
            "task_model": "auto/high-quality",
        }
    )

    assistant = Assistant(config)

    assert assistant.discoverer is None
    assert not hasattr(assistant, "_candidate_endpoints")
    assert not hasattr(assistant, "_provider_for")
    assert not hasattr(assistant, "_mark_failed")


def test_deployment_has_one_inference_authority() -> None:
    root = Path(__file__).resolve().parents[1]
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    env_example = (root / ".env.example").read_text(encoding="utf-8")

    for retired in _RETIRED_ENV_VARS:
        assert retired not in compose
        assert retired not in env_example
    assert "100.100.100.100" not in compose
    assert "SOPHIA_INTENT_MODEL: ${SOPHIA_INTENT_MODEL:-auto/fast}" in compose
    assert "SOPHIA_TASK_MODEL: ${SOPHIA_TASK_MODEL:-auto/high-quality}" in compose
    assert "SOPHIA_INTENT_BASE_URL: ${SOPHIA_INTENT_BASE_URL:-http://host.docker.internal:8088/v1}" in compose
    assert not (root / "src/voice_agent/llm/model_discovery.py").exists()
