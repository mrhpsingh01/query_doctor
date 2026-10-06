"""Progress-event transport shared by the API (SSE) and the Celery workers.

Events are plain dicts published under a string key (``analysis:<id>``, ``benchmark:<id>``). Readers resume after a
previously seen event id, so a late SSE subscriber replays history. Redis Streams in production; an in-memory
implementation for tests and single-process development.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Protocol

Event = dict[str, Any]


class EventBus(Protocol):
    def publish(self, key: str, event: Event) -> None: ...

    def read(self, key: str, after: str | None, block_ms: int) -> list[tuple[str, Event]]:
        """Events after ``after`` (None = from the start). Waits up to ``block_ms`` for new ones; 0 = don't wait."""
        ...


class InMemoryEventBus:
    def __init__(self) -> None:
        self._events: dict[str, list[Event]] = {}
        self._cond = threading.Condition()

    def publish(self, key: str, event: Event) -> None:
        with self._cond:
            self._events.setdefault(key, []).append(dict(event))
            self._cond.notify_all()

    def read(self, key: str, after: str | None, block_ms: int) -> list[tuple[str, Event]]:
        start = int(after) if after else 0
        with self._cond:
            if len(self._events.get(key, [])) <= start and block_ms > 0:
                self._cond.wait_for(lambda: len(self._events.get(key, [])) > start, timeout=block_ms / 1000)
            events = self._events.get(key, [])
            return [(str(i + 1), e) for i, e in enumerate(events) if i >= start]


class RedisEventBus:
    TTL_SECONDS = 24 * 3600
    MAX_EVENTS = 1000

    def __init__(self, url: str) -> None:
        import redis

        self._redis = redis.Redis.from_url(url, decode_responses=True)

    @staticmethod
    def _stream(key: str) -> str:
        return f"qd:events:{key}"

    def publish(self, key: str, event: Event) -> None:
        stream = self._stream(key)
        self._redis.xadd(stream, {"data": json.dumps(event)}, maxlen=self.MAX_EVENTS, approximate=True)
        self._redis.expire(stream, self.TTL_SECONDS)

    def read(self, key: str, after: str | None, block_ms: int) -> list[tuple[str, Event]]:
        result = self._redis.xread({self._stream(key): after or "0"}, block=block_ms or None, count=100)
        if not result:
            return []
        return [(entry_id, json.loads(fields["data"])) for entry_id, fields in result[0][1]]


_memory_bus = InMemoryEventBus()


def get_event_bus(redis_url: str) -> EventBus:
    """``memory://`` selects the process-wide in-memory bus (tests, single-process dev)."""
    return _memory_bus if redis_url.startswith("memory://") else RedisEventBus(redis_url)
