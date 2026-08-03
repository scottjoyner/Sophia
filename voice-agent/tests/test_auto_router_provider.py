from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from voice_agent.llm.openai_compat_provider import (
    LLMProviderError,
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
    status_code: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    lines: list[bytes] = field(default_factory=list)

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload

    def iter_lines(self):
        yield from self.lines


def test_task_alias_uses_single_v1_path_and_high_quality_model(monkeypatch) -> None:
    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["payload"] = json
        captured["headers"] = headers
        return _Response(
            {
                "choices": [{"message": {"content": "[]"}}],
                "auto_router": {
                    "provider": "xwing-lmstudio",
                    "model": "qwen-local",
                    "stage": "final",
                    "profile": "high-quality",
                    "latency_ms": 321,
                },
            }
        )

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

    response = provider.complete(
        "extract tasks",
        request_metadata={"session_id": "meeting-1"},
    )

    assert response.content == "[]"
    assert captured["url"] == "http://auto-router:8088/v1/chat/completions"
    assert captured["payload"]["model"] == "auto/high-quality"
    assert captured["headers"]["Authorization"] == "Bearer local-offline-only"
    metadata = captured["payload"]["metadata"]
    assert metadata["workload_class"] == "task_extraction"
    assert metadata["session_id"] == "meeting-1"
    assert metadata["privacy"] == "personal"
    assert metadata["local_only"] is True
    assert metadata["allow_cloud"] is False
    assert metadata["latency_target_ms"] == 10_000
    assert metadata["correlation_id"]
    assert response.metadata["provider"] == "xwing-lmstudio"
    assert response.metadata["model"] == "qwen-local"
    assert response.metadata["profile"] == "high-quality"
    assert provider.last_route_metadata == response.metadata


def test_stream_records_router_headers_and_ttft(monkeypatch) -> None:
    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        captured["payload"] = json
        return _Response(
            {},
            headers={
                "X-Auto-Router-Provider": "destroyer-lmstudio",
                "X-Auto-Router-Model": "qwen-fast",
                "X-Auto-Router-Stage": "draft",
                "X-Auto-Router-Profile": "fast",
            },
            lines=[
                b'data: {"choices":[{"delta":{"content":"hello"}}]}',
                b"data: [DONE]",
            ],
        )

    monkeypatch.setattr(
        "voice_agent.llm.openai_compat_provider.requests.post",
        fake_post,
    )
    provider = OpenAICompatProvider(
        "http://auto-router:8088",
        None,
        "auto/fast",
    )

    chunks = list(
        provider.stream_complete(
            [{"role": "user", "content": "hello"}],
            request_metadata={"session_id": "voice-7"},
        )
    )

    assert chunks == ["hello"]
    assert captured["payload"]["metadata"]["workload_class"] == "interactive_voice"
    assert captured["payload"]["metadata"]["session_id"] == "voice-7"
    assert captured["payload"]["metadata"]["latency_target_ms"] == 1_500
    assert provider.last_route_metadata["provider"] == "destroyer-lmstudio"
    assert provider.last_route_metadata["model"] == "qwen-fast"
    assert provider.last_route_metadata["stage"] == "draft"
    assert provider.last_route_metadata["profile"] == "fast"
    assert provider.last_route_metadata["ttft_ms"] >= 0
    assert provider.last_route_metadata["latency_ms"] >= 0


def test_non_router_payload_does_not_add_router_metadata(monkeypatch) -> None:
    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        captured["payload"] = json
        return _Response({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(
        "voice_agent.llm.openai_compat_provider.requests.post",
        fake_post,
    )
    provider = OpenAICompatProvider(
        "https://api.openai.com/v1",
        "secret",
        "gpt-example",
    )

    provider.complete("hello")

    assert "metadata" not in captured["payload"]


def test_router_503_is_structured_and_never_implies_hosted_fallback(monkeypatch) -> None:
    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        return _Response(
            {"detail": {"error": "no admitted candidates"}},
            status_code=503,
            headers={"Retry-After": "12"},
        )

    monkeypatch.setattr(
        "voice_agent.llm.openai_compat_provider.requests.post",
        fake_post,
    )
    provider = OpenAICompatProvider(
        "http://auto-router:8088",
        None,
        "auto/fast",
    )

    with pytest.raises(LLMProviderError) as exc_info:
        provider.complete("hello")

    exc = exc_info.value
    assert exc.status_code == 503
    assert exc.retry_after_seconds == 12
    assert "not sent to a hosted provider" in str(exc)
    assert provider.last_route_metadata["status_code"] == 503
    assert provider.last_route_metadata["retry_after_seconds"] == 12
