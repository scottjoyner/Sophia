from __future__ import annotations

from voice_agent.config import AppConfig
from voice_agent.llm.provider_base import LLMResponse
from voice_agent.server.assistant import Assistant, _parse_task_list


class _FakeProvider:
    def complete(self, prompt, timeout=None):
        return LLMResponse(content='[{"title":"Call mom","priority":"high","due":null,"assignee":null}]')

    def stream_complete(self, messages, **kwargs):
        yield "[mock] echo"


def test_parse_task_list_empty_inputs() -> None:
    assert _parse_task_list("") == []
    assert _parse_task_list("   ") == []
    assert _parse_task_list("[]") == []


def test_parse_task_list_valid_array() -> None:
    raw = '[{"title":"Book flight","priority":"low","due":"2026-08-01","assignee":"bob"}]'
    parsed = _parse_task_list(raw)
    assert len(parsed) == 1
    assert parsed[0]["title"] == "Book flight"
    assert parsed[0]["priority"] == "low"
    assert parsed[0]["due"] == "2026-08-01"
    assert parsed[0]["assignee"] == "bob"


def test_parse_task_list_fenced_and_prose() -> None:
    raw = 'Sure! Here you go:\n```json\n[{"title":"Water plants"}]\n```'
    parsed = _parse_task_list(raw)
    assert len(parsed) == 1
    assert parsed[0]["title"] == "Water plants"
    assert parsed[0]["priority"] == "medium"


def test_parse_task_list_invalid_returns_empty() -> None:
    assert _parse_task_list("not json at all") == []


def test_parse_task_list_normalizes_priority_and_title() -> None:
    parsed = _parse_task_list('[{"description":"do the thing"}]')
    assert parsed[0]["title"] == "do the thing"
    assert parsed[0]["priority"] == "medium"


def test_assistant_stream_reply_uses_mock_provider() -> None:
    assistant = Assistant(AppConfig())
    tokens = list(assistant.stream_reply([{"role": "user", "content": "hi"}]))
    assert tokens
    assert "[mock]" in tokens[0]


def test_assistant_extract_tasks_parses_provider_output() -> None:
    assistant = Assistant(AppConfig())
    assistant.provider = _FakeProvider()
    tasks = assistant.extract_tasks("remind me to call mom")
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Call mom"


def test_assistant_ingest_tasks_builds_dispatch() -> None:
    assistant = Assistant(AppConfig())
    results = assistant.ingest_tasks([{"title": "Send report", "priority": "medium"}])
    assert len(results) == 1
    assert "dispatch" in results[0]
    assert results[0]["task"]["title"] == "Send report"


def test_assistant_format_context() -> None:
    assistant = Assistant(AppConfig())
    ctx = assistant._format_context(
        {
            "location": {"lat": 1.0, "lng": 2.0, "accuracy_m": 10},
            "platform": "web",
            "device_id": "dev1",
            "timezone": "UTC",
            "activity": "walking",
            "note": "context note",
        }
    )
    assert "1.0, 2.0" in ctx
    assert "web" in ctx
    assert "dev1" in ctx
    assert "walking" in ctx
    assert "context note" in ctx


def test_assistant_prepare_messages_injects_images() -> None:
    assistant = Assistant(AppConfig())
    msgs = assistant._prepare_messages(
        [{"role": "user", "content": "what is this?"}],
        {"images": [{"data": "abc", "media_type": "image/png"}]},
    )
    user_msg = next(m for m in msgs if m.get("role") == "user")
    assert isinstance(user_msg["content"], list)
    assert user_msg["content"][1]["type"] == "image_url"
