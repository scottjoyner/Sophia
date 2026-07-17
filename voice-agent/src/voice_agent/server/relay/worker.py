from __future__ import annotations

import asyncio
from typing import Any


class RelayTurnWorker:
    """Async transcript-to-assistant bridge for relay sessions.

    Relay HTTP/WebSocket handlers only persist transcript input and enqueue a turn.
    This worker runs the slower assistant path out of band and emits durable relay
    output/error events through the broker.
    """

    def __init__(self, broker: Any, manager: Any, *, max_queue: int = 200):
        self.broker = broker
        self.manager = manager
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_queue)
        self._task: asyncio.Task[None] | None = None
        self._queued_ids: set[int] = set()
        self.started = False

    def start(self) -> None:
        if self.started:
            return
        self.started = True
        self._enqueue_pending()
        self._task = asyncio.create_task(self._run(), name="relay-turn-worker")

    async def stop(self) -> None:
        self.started = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def enqueue(self, payload: dict[str, Any]) -> bool:
        transcript_id = int(payload.get("transcript_id") or 0)
        if transcript_id and transcript_id in self._queued_ids:
            return True
        try:
            self.queue.put_nowait(payload)
            if transcript_id:
                self._queued_ids.add(transcript_id)
            return True
        except asyncio.QueueFull:
            return False

    def status(self) -> dict[str, Any]:
        return {"started": self.started, "queue_depth": self.queue.qsize(), "max_queue": self.queue.maxsize}

    def _enqueue_pending(self) -> None:
        remaining = max(0, self.queue.maxsize - self.queue.qsize())
        if remaining == 0:
            return
        for item in self.broker.pending_transcripts(limit=remaining).get("items", []):
            self.enqueue(
                {
                    "transcript_id": item["id"],
                    "session_id": item["session_id"],
                    "device_id": item["device_id"],
                    "seq": item["seq"],
                    "text": item["text"],
                }
            )

    async def _run(self) -> None:
        while self.started:
            self._enqueue_pending()
            item = await self.queue.get()
            transcript_id = int(item.get("transcript_id") or 0)
            try:
                response = await asyncio.to_thread(
                    self.manager.pipeline.ralph.run,
                    f"Voice relay input from session {item['session_id']} on device {item['device_id']}: {item['text']}",
                )
                self.broker.complete_transcript(
                    item["transcript_id"],
                    item["session_id"],
                    item["device_id"],
                    response_text=response,
                )
            except Exception as exc:  # durable transcript is preserved; failure is observable and retried after restart/requeue.
                self.broker.complete_transcript(
                    item["transcript_id"],
                    item["session_id"],
                    item["device_id"],
                    error=f"{type(exc).__name__}: {exc}",
                )
            finally:
                if transcript_id:
                    self._queued_ids.discard(transcript_id)
                self.queue.task_done()
