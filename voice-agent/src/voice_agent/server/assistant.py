from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, Iterator, List, Optional

from ..config import AppConfig
from ..llm.openai_compat_provider import OpenAICompatProvider
from ..llm.provider_base import LLMProvider, MockProvider
from .assistx_dispatch import build_voice_event, dispatch_to_assistx


TASK_EXTRACTION_PROMPT = (
    "You are a task extraction engine for a personal assistant. "
    "Read the conversation and extract any concrete tasks, to-dos, or requests the user wants done. "
    "Respond with ONLY a JSON array and nothing else — no prose, no markdown, no code fences. "
    "Each item is an object with exactly these keys: "
    "title (short string), description (one sentence), priority (one of low|medium|high), "
    "due (ISO date string or null), assignee (string or null). "
    "If there are no tasks, return an empty array [].\n\n"
    "Example output:\n"
    "[{\"title\":\"Book dentist appointment\",\"description\":\"Schedule a dentist visit for next Tuesday.\","
    "\"priority\":\"medium\",\"due\":\"2026-07-21\",\"assignee\":null}]\n\n"
    "CONVERSATION:\n{conversation}"
)


class Assistant:
    def __init__(self, config: AppConfig):
        self.config = config
        self.provider = self._build_provider()

    def _build_provider(self) -> LLMProvider:
        llm = self.config.llm
        if llm.intent_provider in {"openai", "hermes"} and llm.intent_base_url:
            return OpenAICompatProvider(
                llm.intent_base_url,
                llm.intent_api_key or llm.api_key,
                llm.intent_model,
                timeout=llm.timeout,
                task_model=llm.task_model,
            )
        if llm.provider in {"openai", "hermes"} and llm.base_url:
            return OpenAICompatProvider(
                llm.base_url,
                llm.api_key,
                llm.model,
                timeout=llm.timeout,
                task_model=llm.task_model,
            )
        return MockProvider()

    @property
    def configured(self) -> bool:
        return isinstance(self.provider, OpenAICompatProvider)

    @property
    def model_label(self) -> str:
        if isinstance(self.provider, OpenAICompatProvider):
            return self.provider.model
        return "mock"

    def stream_reply(self, messages: List[Dict[str, str]]) -> Iterator[str]:
        gen = self.provider.stream_complete(messages)
        if isinstance(gen, str):
            yield gen
            return
        yield from gen

    def extract_tasks(self, conversation: str) -> List[Dict[str, Any]]:
        if not conversation.strip():
            return []
        prompt = TASK_EXTRACTION_PROMPT.replace("{conversation}", conversation)
        try:
            raw = self.provider.complete(prompt).content
        except Exception:
            return []
        return _parse_task_list(raw)

    def ingest_tasks(
        self,
        tasks: List[Dict[str, Any]],
        *,
        session_id: Optional[str] = "console",
        actor: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for task in tasks:
            text = task.get("title") or task.get("description") or "Untitled task"
            metadata = {
                "task": task,
                "user_id": (actor or {}).get("user_id", "scott"),
                "device_id": (actor or {}).get("device_id"),
            }
            payload = build_voice_event(
                "task_created",
                text,
                metadata,
                session_id=session_id,
                auto_dispatch=True,
                actor=actor,
            )
            dispatch = dispatch_to_assistx(payload)
            results.append(
                {
                    "task": task,
                    "event_id": payload.get("event_id"),
                    "correlation_id": payload.get("correlation_id"),
                    "dispatch": dispatch,
                }
            )
        return results


def _parse_task_list(raw: str) -> List[Dict[str, Any]]:
    if not raw:
        return []
    text = raw.strip()
    # Strip markdown code fences if the model wrapped the JSON.
    fenced = re.search(r"```(?:json)?\s*(\[.*\])\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        candidate = text
    # Try the whole payload first, then fall back to the first [...] block.
    for attempt in (candidate,):
        try:
            data = json.loads(attempt)
            if isinstance(data, list):
                break
        except Exception:
            data = None
    if data is None:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except Exception:
            return []
    if not isinstance(data, list):
        return []
    cleaned: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip() or str(item.get("description") or "").strip() or "Untitled task"
        priority = str(item.get("priority") or "medium").lower()
        if priority not in {"low", "medium", "high"}:
            priority = "medium"
        cleaned.append(
            {
                "title": title,
                "description": str(item.get("description") or "").strip(),
                "priority": priority,
                "due": item.get("due"),
                "assignee": item.get("assignee"),
            }
        )
    return cleaned
