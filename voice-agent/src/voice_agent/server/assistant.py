from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, Iterable, Iterator, List, Optional

from ..config import AppConfig
from ..llm.openai_compat_provider import LLMProviderError, OpenAICompatProvider
from ..llm.provider_base import LLMProvider, MockProvider
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
        self._provider_cache: Dict[str, LLMProvider] = {}
        self._failed_endpoints: Dict[str, float] = {}
        self._endpoint_cooldown = 120.0
        if getattr(config.llm, "fleet_discovery", False):
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
            import logging

            logging.getLogger(__name__).warning("fleet discovery failed to start: %s", exc)
            self.discoverer = None

    def _provider_for(self, ep) -> Optional[LLMProvider]:
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

    def _format_context(self, context: Optional[Dict[str, Any]]) -> str:
        if not context:
            return ""
        parts: List[str] = []
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

    def _prepare_messages(self, messages: List[Dict[str, Any]], context: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        msgs: List[Dict[str, Any]] = [dict(m) for m in messages]
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
                    content: List[Dict[str, Any]] = [{"type": "text", "text": text}]
                    for img in images:
                        url = img.get("url")
                        if not url and img.get("data"):
                            url = "data:" + img.get("media_type", "image/png") + ";base64," + img["data"]
                        if url:
                            content.append({"type": "image_url", "image_url": {"url": url}})
                    m["content"] = content
                    break
        return msgs

    def stream_reply(self, messages: List[Dict[str, Any]], context: Optional[Dict[str, Any]] = None) -> Iterator[str]:
        msgs = self._prepare_messages(messages, context)
        eps = self._candidate_endpoints("chat")
        if not eps:
            try:
                yield from self.provider.stream_complete(msgs)
                return
            except Exception as exc:
                logger.warning("configured chat provider failed: %s", exc)
                raise
        for ep in eps:
            provider = self._provider_for(ep) or self.provider
            try:
                for delta in provider.stream_complete(msgs):
                    yield delta
                self.discoverer._mark_used(ep)
                return
            except Exception as exc:
                self._mark_failed(ep)
                logger.warning("chat candidate %s failed: %s; trying next", ep.full_id, exc)
                continue
        raise LLMProviderError("all chat endpoints failed")

    def extract_tasks(self, conversation: str, context: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        if not conversation.strip():
            return []
        prompt = TASK_EXTRACTION_PROMPT.replace("{conversation}", conversation)
        # Task extraction is a short structured-JSON call. Prefer the largest
        # (heaviest) model for best quality, but fail fast and degrade to a
        # chat-sized model if the big one is cold/unreachable, so extraction
        # always produces something instead of hanging or returning nothing.
        extract_timeout = self.config.llm.task_extract_timeout or 30
        task_eps = self._candidate_endpoints("task") or []
        chat_eps = self._candidate_endpoints("chat") or []
        pairs = [(ep, self._provider_for(ep) or self.provider) for ep in task_eps]
        pairs += [(ep, self._provider_for(ep) or self.provider) for ep in chat_eps]
        if not pairs:
            pairs = [(None, self.provider)]
        for ep, provider in pairs:
            try:
                raw = provider.complete(prompt, timeout=extract_timeout).content
                if ep is not None:
                    self.discoverer._mark_used(ep)
                return _parse_task_list(raw)
            except Exception as exc:
                if ep is not None:
                    self._mark_failed(ep)
                logger.warning("task candidate failed: %s; trying next", exc)
                continue
        return []

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
