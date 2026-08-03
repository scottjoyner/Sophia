from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from typing import Any

from ..config import AppConfig
from ..llm.openai_compat_provider import OpenAICompatProvider
from ..llm.provider_base import LLMProvider, LLMResponse, MockProvider
from .assistx_dispatch import build_voice_event, dispatch_to_assistx

logger = logging.getLogger(__name__)


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
    """Sophia's logical-workload client.

    Sophia deliberately owns no fleet discovery, endpoint ranking, or retry
    placement. It selects a logical model alias and sends workload/privacy
    metadata; auto-router owns physical admission and routing.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.provider = self._build_provider()
        # Kept as a read-only compatibility attribute for the existing /status
        # response. It is always None; the direct fleet implementation is gone.
        self.discoverer = None
        self._last_chat_endpoint: str | None = None
        self._last_task_endpoint: str | None = None
        self.db = None

    def _build_provider(self) -> LLMProvider:
        llm = self.config.llm
        if llm.intent_provider in {"openai", "hermes"} and llm.intent_base_url:
            return OpenAICompatProvider(
                llm.intent_base_url,
                llm.intent_api_key or llm.api_key,
                llm.intent_model,
                timeout=llm.timeout,
                task_model=llm.task_model,
                task_timeout=llm.task_timeout,
            )
        if llm.provider in {"openai", "hermes"} and llm.base_url:
            return OpenAICompatProvider(
                llm.base_url,
                llm.api_key,
                llm.model,
                timeout=llm.timeout,
                task_model=llm.task_model,
                task_timeout=llm.task_timeout,
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

    def _format_context(self, context: dict[str, Any] | None) -> str:
        if not context:
            return ""
        parts: list[str] = []
        loc = context.get("location") or {}
        if loc.get("lat") is not None:
            value = f"User location: {loc.get('lat')}, {loc.get('lng')}"
            if loc.get("accuracy_m") is not None:
                value += f" (±{loc.get('accuracy_m')} m)"
            parts.append(value)
        if context.get("platform"):
            parts.append(f"Device platform: {context.get('platform')}")
        if context.get("device_id"):
            parts.append(f"Device: {context.get('device_id')}")
        if context.get("timezone"):
            parts.append(f"Timezone: {context.get('timezone')}")
        if context.get("activity"):
            parts.append(f"Activity: {context.get('activity')}")
        if context.get("note"):
            parts.append(str(context.get("note")))
        return "\n".join(parts)

    def _router_request_metadata(
        self,
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(context, dict):
            return {}
        metadata: dict[str, Any] = {}
        for key in (
            "session_id",
            "correlation_id",
            "device_id",
            "user_id",
            "activity",
            "timezone",
        ):
            value = context.get(key)
            if value is not None and value != "":
                metadata[key] = value
        return metadata

    def _route_label(self, provider: LLMProvider, fallback: str) -> str:
        route = getattr(provider, "last_route_metadata", None)
        if not isinstance(route, dict) or not route:
            return fallback
        selected_provider = route.get("provider")
        selected_model = route.get("model") or route.get("requested_model")
        selected = "/".join(
            str(value)
            for value in (selected_provider, selected_model)
            if value
        ) or fallback
        profile = route.get("profile")
        stage = route.get("stage")
        tags = ":".join(str(value) for value in (profile, stage) if value)
        metrics: list[str] = []
        if route.get("ttft_ms") is not None:
            metrics.append(f"ttft={route['ttft_ms']}ms")
        if route.get("latency_ms") is not None:
            metrics.append(f"latency={route['latency_ms']}ms")
        label = f"router:{selected}"
        if tags:
            label += f" [{tags}]"
        if metrics:
            label += " " + " ".join(metrics)
        return label

    def _stream_provider(
        self,
        provider: LLMProvider,
        messages: list[dict[str, Any]],
        request_metadata: dict[str, Any],
    ) -> Iterator[str]:
        if isinstance(provider, OpenAICompatProvider):
            yield from provider.stream_complete(
                messages,
                request_metadata=request_metadata,
            )
            return
        yield from provider.stream_complete(messages)

    def _complete_provider(
        self,
        provider: LLMProvider,
        prompt: str,
        *,
        timeout: float,
        request_metadata: dict[str, Any],
    ) -> LLMResponse:
        if isinstance(provider, OpenAICompatProvider):
            return provider.complete(
                prompt,
                timeout=timeout,
                request_metadata=request_metadata,
            )
        return provider.complete(prompt)

    def _prepare_messages(
        self,
        messages: list[dict[str, Any]],
        context: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        prepared: list[dict[str, Any]] = [dict(message) for message in messages]
        context_text = self._format_context(context)
        if context_text:
            prepared.insert(
                0,
                {
                    "role": "system",
                    "content": (
                        "The following is live context about the user and their environment. "
                        "Use it when relevant to the request; ignore it otherwise:\n"
                        + context_text
                    ),
                },
            )
        images = (context or {}).get("images") if isinstance(context, dict) else None
        if images:
            for message in prepared:
                if message.get("role") != "user":
                    continue
                text = (
                    message.get("content", "")
                    if isinstance(message.get("content"), str)
                    else ""
                )
                content: list[dict[str, Any]] = [{"type": "text", "text": text}]
                for image in images:
                    url = image.get("url")
                    if not url and image.get("data"):
                        url = (
                            "data:"
                            + image.get("media_type", "image/png")
                            + ";base64,"
                            + image["data"]
                        )
                    if url:
                        content.append(
                            {"type": "image_url", "image_url": {"url": url}}
                        )
                message["content"] = content
                break
        return prepared

    def stream_reply(
        self,
        messages: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> Iterator[str]:
        prepared = self._prepare_messages(messages, context)
        request_metadata = self._router_request_metadata(context)
        try:
            yield from self._stream_provider(
                self.provider,
                prepared,
                request_metadata,
            )
            fallback = "config:" + getattr(self.provider, "model", "config")
            self._last_chat_endpoint = self._route_label(self.provider, fallback)
        except Exception as exc:
            logger.warning("configured chat provider failed: %s", exc)
            raise

    def extract_tasks(
        self,
        conversation: str,
        context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not conversation.strip():
            return []
        prompt = TASK_EXTRACTION_PROMPT.replace("{conversation}", conversation)
        extract_timeout = self.config.llm.task_extract_timeout or 30
        request_metadata = self._router_request_metadata(context)
        try:
            response = self._complete_provider(
                self.provider,
                prompt,
                timeout=extract_timeout,
                request_metadata=request_metadata,
            )
            fallback = "config:" + getattr(self.provider, "task_model", "config")
            self._last_task_endpoint = self._route_label(self.provider, fallback)
            return _parse_task_list(response.content)
        except Exception as exc:
            # auto-router owns endpoint retries/admission. Sophia records the
            # failure and returns no extracted tasks rather than probing nodes.
            logger.warning("task extraction provider failed: %s", exc)
            return []

    def ingest_tasks(
        self,
        tasks: list[dict[str, Any]],
        *,
        session_id: str | None = "console",
        actor: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        user_id = (actor or {}).get("user_id")
        device_id = (actor or {}).get("device_id")
        for task in tasks:
            text = task.get("title") or task.get("description") or "Untitled task"
            metadata = {
                "task": task,
                "user_id": user_id,
                "device_id": device_id,
            }
            payload = build_voice_event(
                "task_created",
                text,
                metadata,
                session_id=session_id,
                auto_dispatch=True,
                actor=actor,
            )
            outbox_id = None
            if self.db is not None:
                try:
                    outbox_id = self.db.enqueue_task(
                        user_id=user_id,
                        device_id=device_id,
                        session_id=session_id or "console",
                        event_id=payload.get("event_id"),
                        correlation_id=payload.get("correlation_id"),
                        task_title=text,
                        task_json=task,
                        payload_json=payload,
                    )
                except Exception as exc:
                    logger.warning("failed to enqueue task in outbox: %s", exc)
            dispatch = dispatch_to_assistx(payload)
            if self.db is not None and outbox_id is not None:
                try:
                    self.db.mark_task_dispatched(
                        outbox_id,
                        sent=bool(dispatch.get("sent")),
                        dispatch_id=dispatch.get("dispatch_id"),
                        task_id=dispatch.get("task_id"),
                        response=dispatch.get("response"),
                        error=dispatch.get("error"),
                    )
                except Exception as exc:
                    logger.warning("failed to record task dispatch: %s", exc)
            results.append(
                {
                    "task": task,
                    "event_id": payload.get("event_id"),
                    "correlation_id": payload.get("correlation_id"),
                    "dispatch": dispatch,
                    "outbox_id": outbox_id,
                }
            )
        return results


def _parse_task_list(raw: str) -> list[dict[str, Any]]:
    if not raw:
        return []
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\[.*\])\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    data: Any = None
    try:
        data = json.loads(candidate)
    except Exception:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except Exception:
            return []
    if not isinstance(data, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = (
            str(item.get("title") or "").strip()
            or str(item.get("description") or "").strip()
            or "Untitled task"
        )
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


def reconcile_tasks(
    db,
    *,
    requeue_failed: bool = False,
    max_retries: int = 5,
) -> dict[str, Any]:
    """Report on the ingested-task outbox and optionally requeue dead letters."""

    summary = db.task_summary()
    pending = db.list_tasks(status="pending", limit=100)
    failed = db.list_tasks(status="failed", limit=100)
    retriable = [task for task in failed if (task.get("attempts") or 0) <= max_retries]
    dead_letter = [task for task in failed if (task.get("attempts") or 0) > max_retries]
    requeued = 0
    if requeue_failed:
        for task in retriable:
            try:
                db.requeue_failed_task(task["task_outbox_id"])
                requeued += 1
            except Exception as exc:
                logger.warning(
                    "failed to requeue task %s: %s",
                    task.get("task_outbox_id"),
                    exc,
                )
    return {
        "summary": summary,
        "pending_count": len(pending),
        "failed_count": len(failed),
        "retriable_count": len(retriable),
        "dead_letter_count": len(dead_letter),
        "requeued": requeued,
        "check_only": not requeue_failed,
    }


def retry_failed_tasks(db) -> dict[str, Any]:
    """Re-dispatch every pending and retriable failed task through AssistX."""

    pending = db.list_tasks(status="pending", limit=500)
    failed = db.list_tasks(status="failed", limit=500)
    retriable = [task for task in failed if (task.get("attempts") or 0) <= 5]
    attempted = 0
    succeeded = 0
    for task in pending + retriable:
        payload = task.get("payload_json") or {}
        if not payload:
            continue
        attempted += 1
        dispatch = dispatch_to_assistx(payload)
        db.mark_task_dispatched(
            task["task_outbox_id"],
            sent=bool(dispatch.get("sent")),
            dispatch_id=dispatch.get("dispatch_id"),
            task_id=dispatch.get("task_id"),
            response=dispatch.get("response"),
            error=dispatch.get("error"),
        )
        if dispatch.get("sent"):
            succeeded += 1
    return {
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": attempted - succeeded,
    }
