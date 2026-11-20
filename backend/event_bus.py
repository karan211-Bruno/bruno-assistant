"""In-process Server-Sent Events (SSE) broadcast hub."""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator


class EventBus:
    """Fan-out real-time events across all connected clients (phone, tablet, desktop)."""

    def __init__(self, max_queue_size: int = 128) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._max_queue_size = max_queue_size

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Broadcast an SSE event non-blockingly to every active subscriber."""
        payload = json.dumps(data or {}, ensure_ascii=False)
        frame = f"event: {event_type}\ndata: {payload}\n\n"
        stale: list[asyncio.Queue[str]] = []
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(frame)
            except Exception:
                stale.append(queue)
        for queue in stale:
            self._subscribers.discard(queue)

    async def subscribe(self) -> AsyncIterator[str]:
        """Yield SSE frames plus periodic heartbeats until client disconnects."""
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=self._max_queue_size)
        self._subscribers.add(queue)
        try:
            yield f"event: connected\ndata: {json.dumps({'ok': True})}\n\n"
            while True:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=20.0)
                    yield frame
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            self._subscribers.discard(queue)


event_bus = EventBus()
