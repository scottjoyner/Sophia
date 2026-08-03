from __future__ import annotations

from dataclasses import dataclass

import pytest

from voice_agent.llm.openai_compat_provider import (
    OpenAICompatProvider,
    normalize_openai_base_url,
    validate_auto_router_base_url,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://auto-router:8088", "http://auto-router:8088"),
        ("http://auto-router:8088/", "http://auto-router:8088"),
        ("http://auto-router:8088/v1", "http://auto-router:8088"),
        ("http://host.docker.internal:8088/v1/", "http://host.docker.internal:8088"),
        ("http://127.0.0.1:8088/prefix/v1", "http://127.0.0.1:8088/prefix"),
    ],
)
def test_normalize_openai_base_url(raw: str, expected: str) -> None:
    assert normalize_openai_base_url(raw) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://auto-router:8088/v1",
        "http://host.docker.internal:8088",
        "http://127.0.0.1:8088",
        "http://192.168.1.51:8088",
        "http://100.90.80.70:8088",
        "http://router.lan:8088",
        "https://router.example.ts.net:8088/v1",
    ],
)
def test_auto_router_alias_accepts_private_hosts(url: str) -> None:
    provider = OpenAICompatProvider(
        url,
        "local-only",
        "auto/fast",
        task_model="auto/high-quality",
    )
    assert provider.uses_auto_router is True
    assert not provider.base_url.endswith("/v1")


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "https://openrouter.ai/api/v1",
        "http://8.8.8.8:8088/v1",
        "https://router.example.com/v1",
    ],
)
def test_auto_router_alias_rejects_public_hosts(url: str) -> None:
    with pytest.raises(ValueError, match="require a local"):
        OpenAICompatProvider(url, "secret", "auto/fast")


def test_non_router_model_retains_openai_compatibility() -> None:
    provider = OpenAICompatProvider(
        "https://api.openai.com/v1",
        "secret",
        "gpt-example",
    )
    assert provider.uses_auto_router is False
    assert provider.base_url == "https://api.openai.com"


def test_validate_auto_router_base_url_rejects_invalid_url() -> None:
    with pytest.raises(ValueError, match="Invalid OpenAI-compatible"):
        validate_auto_router_base_url("not-a-url")


@dataclass
class _Response:
    payload: dict

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


def test_task_alias_uses_single_v1_path_and_high_quality_model(monkeypatch) -> None:
    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["payload"] = json
        captured["headers"] = headers
        return _Response({"choices": [{"message": {"content": "[]"}}]})

    monkeypatch.setattr(
        "voice_agent.llm.openai_compat_provider.requests.post",
        fake_post,
    )
    provider = OpenAICompatProvider(
        "http://auto-router:8088/v1",
        "local-offline-only",
        "auto/fast",
        task_model="auto/high-quality",
    )

    response = provider.complete("extract tasks")

    assert response.content == "[]"
    assert captured["url"] == "http://auto-router:8088/v1/chat/completions"
    assert captured["payload"]["model"] == "auto/high-quality"
    assert captured["headers"]["Authorization"] == "Bearer local-offline-only"
