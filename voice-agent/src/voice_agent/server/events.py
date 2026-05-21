from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Iterable, Optional

from ..util.time import now_ms


@dataclass
class EventRecord:
    id: int
    type: str
    payload: Dict[str, Any]


class EventBus:
    """Small in-process event fanout for websocket and dashboard clients."""

    def __init__(self, max_events: int = 1000):
        self._events: Deque[EventRecord] = deque(maxlen=max_events)
        self._subscribers: set[asyncio.Queue[EventRecord]] = set()
        self._next_id = 1

    def publish(self, event_type: str, payload: Dict[str, Any]) -> EventRecord:
        if "ts_ms" not in payload:
            payload = {**payload, "ts_ms": now_ms()}
        record = EventRecord(id=self._next_id, type=event_type, payload=payload)
        self._next_id += 1
        self._events.append(record)
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(record)
            except asyncio.QueueFull:
                pass
        return record

    def snapshot(self, *, after_id: int = 0, session_id: Optional[str] = None) -> list[EventRecord]:
        records: Iterable[EventRecord] = self._events
        if after_id:
            records = (event for event in records if event.id > after_id)
        if session_id:
            records = (event for event in records if event.payload.get("session_id") == session_id)
        return list(records)

    def subscribe(self) -> asyncio.Queue[EventRecord]:
        queue: asyncio.Queue[EventRecord] = asyncio.Queue(maxsize=200)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[EventRecord]) -> None:
        self._subscribers.discard(queue)


def event_to_dict(event: EventRecord) -> Dict[str, Any]:
    return {"id": event.id, "type": event.type, "payload": event.payload}
