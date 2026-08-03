from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator
from typing import Any

from ..config import AppConfig
from ..llm.openai_compat_provider import LLMProviderError, OpenAICompatProvider
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
    def __init__(self, config: AppConfig):
        self.config = config
        self.provider = self._build_provider()
        self.discoverer = None
        self._provider_cache: dict[str, LLMProvider] = {}
        self._failed_endpoints: dict[str, float] = {}
        self._endpoint_cooldown = 120.0
        self._last_chat_endpoint = None
        self._last_task_endpoint = None
        self.db = None
        # W-15: Sophia's local fleet/model selection is disabled by default and
        # delegates to auto-router/auto-assign. Only the explicit
        # SOPHIA_LOCAL_FLEET_DISCOVERY flag (or config local_fleet_discovery)
        # enables the legacy in-tree discoverer.
        if getattr(config.llm, "local_fleet_discovery", False):
            logger.warning(
                "SOPHIA_LOCAL_FLEET_DISCOVERY is deprecated; auto-router and "
                "AssistX must own fleet placement in production"
            )
            self._init_fleet()

    def _init_fleet(self) -> None:
        try:
            from ..llm.model_discovery import ModelDiscoverer

            llm = self.config.llm
            nodes = [n.strip() for n in (llm.fleet_candidate_nodes or "").split(",") if n.strip()]
            self.discoverer = ModelDiscoverer(
                node_port=llm.fleet_node_port,
                router_models_url=llm.fleet_router_url,
                candidate_nodes=nodes or None,
                refresh_interval=llm.fleet_refresh_interval,
                chat_max_params=llm.fleet_chat_max_params,
                task_min_params=llm.fleet_task_min_params,
            )
            self.discoverer.start()
        except Exception as exc:  # discovery is best-effort; fall back to config provider
            logger.warning("fleet discovery failed to start: %s", exc)
            self.discoverer = None

    def _provider_for(self, ep) -> LLMProvider | None:
        if ep is None:
            return None
        key = ep.full_id
        cached = self._provider_cache.get(key)
        if cached is None:
            cached = OpenAICompatProvider(
                ep.base_url,
                None,
                ep.model_id,
                timeout=self.config.llm.timeout,
                task_timeout=self.config.llm.task_timeout,
            )
            self._provider_cache[key] = cached
        return cached

    def _mark_failed(self, ep) -> None:
        if ep is not None:
            self._failed_endpoints[ep.full_id] = time.time()

    def _cooling(self, ep) -> bool:
        ts = self._failed_endpoints.get(ep.full_id)
        return ts is not None and (time.time() - ts) < self._endpoint_cooldown

    def _candidate_endpoints(self, kind: str):
        """Ordered fleet endpoints to try for a workload, best first.

        Chat-suited models (small/fast, idle-preferring) or task-suited models
        (large, idle-preferring) are returned so a single stale/unloaded model
        never breaks the chat — the caller retries the next candidate. Endpoints
        that just failed are skipped (cooldown) so retries don't immediately
        re-hit a dead/slow model. Returns ``None`` when discovery is disabled
        or currently empty (caller falls back to the configured provider)."""
        if not self.discoverer:
            return None
        eps = [e for e in self.discoverer.endpoints() if not e.is_embedding]
        if not eps:
            # Transient empty fleet (e.g. all nodes briefly unreachable) — force
            # one rediscovery before giving up so we self-heal instead of failing.
            try:
                eps = [e for e in self.discoverer.discover(force=True) if not e.is_embedding]
            except Exception:
                eps = []
        if not eps:
            return None
        now = time.time()
        if kind == "chat":
            cands = [e for e in eps if e.params <= self.discoverer.chat_max_params] or eps
            cands.sort(key=lambda e: (self.discoverer._idle_score(e, now), -e.params), reverse=True)
        else:
            cands = [e for e in eps if e.params >= self.discoverer.task_min_params] or eps
            cands.sort(key=lambda e: (e.params, self.discoverer._idle_score(e, now)), reverse=True)
        # Skip endpoints in cooldown unless that would leave us with nothing.
        usable = [e for e in cands if not self._cooling(e)]
        return usable or cands

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
            s = f"User location: {loc.get('lat')}, {loc.get('lng')}"
            if loc.get("accuracy_m") is not None:
                s += f" (±{loc.get('accuracy_m')} m)"
            parts.append(s)
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

    def _prepare_messages(self, messages: list[dict[str, Any]], context: dict[str, Any] | None) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = [dict(m) for m in messages]
        ctx_str = self._format_context(context)
        if ctx_str:
            msgs.insert(0, {
                "role": "system",
                "content": (
                    "The following is live context about the user and their environment. "
                    "Use it when relevant to the request; ignore it otherwise:\n" + ctx_str
                ),
            })
        # Forward-looking: if the client attaches images, render them as
        # multimodal content parts on the first user turn so a future vision
        # model can consume them. Current small chat models will simply ignore
        # or skip them.
        images = (context or {}).get("images") if isinstance(context, dict) else None
        if images:
            for m in msgs:
                if m.get("role") == "user":
                    text = m.get("content", "") if isinstance(m.get("content"), str) else ""
                    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
                    for img in images:
                        url = img.get("url")
                        if not url and img.get("data"):
                            url = "data:" + img.get("media_type", "image/png") + ";base64," + img["data"]
                        if url:
                            content.append({"type": "image_url", "image_url": {"url": url}})
                    m["content"] = content
                    break
        return msgs

    def stream_reply(self, messages: list[dict[str, Any]], context: dict[str, Any] | None = None) -> Iterator[str]:
        msgs = self._prepare_messages(messages, context)
        request_metadata = self._router_request_metadata(context)
        eps = self._candidate_endpoints("chat")
        if not eps:
            try:
                yield from self._stream_provider(
                    self.provider,
                    msgs,
                    request_metadata,
                )
                fallback = "config:" + getattr(self.provider, "model", "config")
                self._last_chat_endpoint = self._route_label(self.provider, fallback)
                return
            except Exception as exc:
                logger.warning("configured chat provider failed: %s", exc)
                raise
        for ep in eps:
            provider = self._provider_for(ep) or self.provider
            try:
                yield from self._stream_provider(provider, msgs, request_metadata)
                self.discoverer._mark_used(ep)
                self._last_chat_endpoint = self._route_label(provider, ep.full_id)
                return
            except Exception as exc:
                self._mark_failed(ep)
                logger.warning("chat candidate %s failed: %s; trying next", ep.full_id, exc)
                continue
        raise LLMProviderError("all chat endpoints failed")

    def extract_tasks(self, conversation: str, context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if not conversation.strip():
            return []
        prompt = TASK_EXTRACTION_PROMPT.replace("{conversation}", conversation)
        # Task extraction is a short structured-JSON call. Prefer the largest
        # (heaviest) model for best quality, but fail fast and degrade to a
        # chat-sized model if the big one is cold/unreachable, so extraction
        # always produces something instead of hanging or returning nothing.
        extract_timeout = self.config.llm.task_extract_timeout or 30
        request_metadata = self._router_request_metadata(context)
        task_eps = self._candidate_endpoints("task") or []
        chat_eps = self._candidate_endpoints("chat") or []
        pairs = [(ep, self._provider_for(ep) or self.provider) for ep in task_eps]
        pairs += [(ep, self._provider_for(ep) or self.provider) for ep in chat_eps]
        if not pairs:
            pairs = [(None, self.provider)]
        for ep, provider in pairs:
            try:
                response = self._complete_provider(
                    provider,
                    prompt,
                    timeout=extract_timeout,
                    request_metadata=request_metadata,
                )
                if ep is not None:
                    self.discoverer._mark_used(ep)
                    self._last_task_endpoint = self._route_label(provider, ep.full_id)
                else:
                    fallback = "config:" + getattr(self.provider, "model", "config")
                    self._last_task_endpoint = self._route_label(provider, fallback)
                return _parse_task_list(response.content)
            except Exception as exc:
                if ep is not None:
                    self._mark_failed(ep)
                logger.warning("task candidate failed: %s; trying next", exc)
                continue
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
    cleaned: list[dict[str, Any]] = []
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


def reconcile_tasks(db, *, requeue_failed: bool = False, max_retries: int = 5) -> dict[str, Any]:
    """Report on the ingested-task outbox and optionally requeue dead letters.

    Mirrors the drift-report shape used by ``reconcile_to_neo4j`` so operators
    get a consistent view of what was dispatched vs. what is stuck.
    """
    summary = db.task_summary()
    pending = db.list_tasks(status="pending", limit=100)
    failed = db.list_tasks(status="failed", limit=100)
    retriable = [t for t in failed if (t.get("attempts") or 0) <= max_retries]
    dead_letter = [t for t in failed if (t.get("attempts") or 0) > max_retries]
    requeued = 0
    if requeue_failed:
        for t in retriable:
            try:
                db.requeue_failed_task(t["task_outbox_id"])
                requeued += 1
            except Exception as exc:
                logger.warning("failed to requeue task %s: %s", t.get("task_outbox_id"), exc)
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
    """Re-dispatch every pending + retriable failed task through AssistX."""
    pending = db.list_tasks(status="pending", limit=500)
    failed = db.list_tasks(status="failed", limit=500)
    retriable = [t for t in failed if (t.get("attempts") or 0) <= 5]
    attempted = 0
    succeeded = 0
    for t in pending + retriable:
        payload = t.get("payload_json") or {}
        if not payload:
            continue
        attempted += 1
        dispatch = dispatch_to_assistx(payload)
        db.mark_task_dispatched(
            t["task_outbox_id"],
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
