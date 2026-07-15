from __future__ import annotations

from voice_agent.config import AppConfig
from voice_agent.server.assistant import Assistant


def _assistant_with(content: str) -> Assistant:
    a = Assistant(AppConfig())
    a.provider.complete = lambda prompt, timeout=None: type("R", (), {"content": content})()
    return a


def test_extraction_prompt_braces_do_not_raise():
    # The task prompt contains a JSON example with {...} braces; .format() would
    # raise KeyError. extract_tasks must substitute safely (no crash).
    a = _assistant_with(
        '[{"title":"Book dentist","description":"Schedule it.","priority":"medium","due":null,"assignee":null}]'
    )
    tasks = a.extract_tasks("user: please book a dentist appointment")
    assert tasks and tasks[0]["title"] == "Book dentist"


def test_extraction_prose_with_embedded_json():
    a = _assistant_with(
        "Sure! Here you go: "
        '[{"title":"Do thing","description":"","priority":"low","due":null,"assignee":null}]'
        " hope that helps"
    )
    tasks = a.extract_tasks("user: do thing")
    assert tasks and tasks[0]["title"] == "Do thing"


def test_extraction_fenced_json():
    a = _assistant_with(
        '```json\n'
        '[{"title":"Send report","description":"to Bob","priority":"high","due":null,"assignee":"Bob"}]'
        '\n```'
    )
    tasks = a.extract_tasks("user: send the report to Bob")
    assert tasks and tasks[0]["title"] == "Send report"


def test_extraction_no_tasks():
    a = _assistant_with("There are no tasks here.")
    assert a.extract_tasks("user: how is the weather") == []


def test_extraction_empty_conversation():
    assert Assistant(AppConfig()).extract_tasks("   ") == []
