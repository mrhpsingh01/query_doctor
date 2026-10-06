import threading
import time
import uuid

import pytest

from app.services.events import InMemoryEventBus, RedisEventBus


def test_publish_and_read_history():
    bus = InMemoryEventBus()
    bus.publish("analysis:1", {"type": "a"})
    bus.publish("analysis:1", {"type": "b"})
    bus.publish("analysis:2", {"type": "other"})
    events = bus.read("analysis:1", after=None, block_ms=0)
    assert [e["type"] for _id, e in events] == ["a", "b"]
    assert bus.read("analysis:3", after=None, block_ms=0) == []


def test_read_after_returns_only_newer_events():
    bus = InMemoryEventBus()
    bus.publish("k", {"type": "a"})
    first_id = bus.read("k", None, 0)[0][0]
    bus.publish("k", {"type": "b"})
    assert [e["type"] for _i, e in bus.read("k", after=first_id, block_ms=0)] == ["b"]


def test_blocking_read_wakes_on_publish():
    bus = InMemoryEventBus()
    threading.Timer(0.1, lambda: bus.publish("k", {"type": "late"})).start()
    started = time.monotonic()
    events = bus.read("k", None, block_ms=2000)
    assert [e["type"] for _i, e in events] == ["late"]
    assert time.monotonic() - started < 1.5


def test_blocking_read_times_out_empty():
    bus = InMemoryEventBus()
    started = time.monotonic()
    assert bus.read("k", None, block_ms=150) == []
    assert 0.1 <= time.monotonic() - started < 1.0


@pytest.mark.integration
def test_redis_bus_round_trip_history_and_isolation():
    bus = RedisEventBus("redis://localhost:6379/0")
    key = f"test:{uuid.uuid4()}"
    bus.publish(key, {"type": "a", "n": 1})
    bus.publish(key, {"type": "b"})
    history = bus.read(key, None, 0)
    assert [e["type"] for _i, e in history] == ["a", "b"]
    assert history[0][1]["n"] == 1
    assert [e["type"] for _i, e in bus.read(key, after=history[0][0], block_ms=0)] == ["b"]
    assert bus.read(f"test:{uuid.uuid4()}", None, 0) == []
