from types import SimpleNamespace

import pytest

from src.agents.memory.queue import MemoryUpdateQueue


class _DummyTimer:
    def __init__(self, _delay, _fn):
        self._cancelled = False

    def start(self):
        return None

    def cancel(self):
        self._cancelled = True


def test_due_times_are_per_thread_and_capped(monkeypatch):
    from src.agents.memory import queue as queue_module

    queue = MemoryUpdateQueue()
    now = {"value": 0.0}

    monkeypatch.setattr(queue_module, "get_memory_config", lambda: SimpleNamespace(enabled=True, debounce_seconds=1))
    monkeypatch.setattr(queue_module.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(queue_module.threading, "Timer", _DummyTimer)

    queue.add("thread-a", ["a-1"])
    assert queue._due_at["thread-a"] == pytest.approx(1.0)

    now["value"] = 0.2
    queue.add("thread-b", ["b-1"])
    assert queue._due_at["thread-b"] == pytest.approx(1.2)

    now["value"] = 3.5
    queue.add("thread-a", ["a-2"])
    assert queue._due_at["thread-a"] == pytest.approx(4.0)
    assert queue._due_at["thread-b"] == pytest.approx(1.2)


def test_process_due_only_drains_ready_threads(monkeypatch):
    from src.agents.memory import queue as queue_module

    queue = MemoryUpdateQueue()
    now = {"value": 0.0}

    monkeypatch.setattr(queue_module, "get_memory_config", lambda: SimpleNamespace(enabled=True, debounce_seconds=1))
    monkeypatch.setattr(queue_module.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(queue_module.threading, "Timer", _DummyTimer)

    processed = []
    monkeypatch.setattr(queue, "_process_contexts", lambda contexts: processed.extend([c.thread_id for c in contexts]))

    queue.add("thread-a", ["a-1"])
    now["value"] = 0.2
    queue.add("thread-b", ["b-1"])

    now["value"] = 1.05
    queue._process_due()

    assert processed == ["thread-a"]
    assert queue.pending_count == 1
    assert "thread-b" in queue._pending


def test_add_copies_message_list(monkeypatch):
    from src.agents.memory import queue as queue_module

    queue = MemoryUpdateQueue()
    monkeypatch.setattr(queue_module, "get_memory_config", lambda: SimpleNamespace(enabled=True, debounce_seconds=1))
    monkeypatch.setattr(queue_module.threading, "Timer", _DummyTimer)

    messages = ["a-1"]
    queue.add("thread-a", messages)
    messages.append("a-2")

    assert queue._pending["thread-a"].messages == ["a-1"]
