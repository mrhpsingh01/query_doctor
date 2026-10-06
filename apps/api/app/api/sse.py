from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator

from fastapi.responses import StreamingResponse

from app.services.events import EventBus

SSE_KEEPALIVE_MS = 15_000
SSE_MAX_SECONDS = 600


def sse_stream(
    bus: EventBus,
    key: str,
    terminal: set[str],
    last_event_id: str | None,
    terminal_status: Callable[[], str | None],
) -> StreamingResponse:
    """Server-sent events for one job. Replays history, follows new events and closes after a terminal one.

    ``terminal_status`` reports the database's truth when the bus has nothing (events expired or never published), so
    a subscriber never hangs on a job that has already finished.
    """

    def generate() -> Iterator[str]:
        after = last_event_id
        deadline = time.monotonic() + SSE_MAX_SECONDS
        while time.monotonic() < deadline:
            batch = bus.read(key, after, block_ms=SSE_KEEPALIVE_MS)
            if not batch:
                final = terminal_status()
                if final:
                    yield f"event: progress\ndata: {json.dumps({'type': final})}\n\n"
                    return
                yield ": keepalive\n\n"
                continue
            for event_id, event in batch:
                after = event_id
                yield f"id: {event_id}\nevent: progress\ndata: {json.dumps(event)}\n\n"
                if event.get("type") in terminal:
                    return

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
