"""Memory update queue with debounce mechanism."""

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from src.config.memory_config import get_memory_config

logger = logging.getLogger(__name__)

CN_TIMEZONE = timezone(timedelta(hours=8))


def _now_cn() -> datetime:
    return datetime.now(CN_TIMEZONE)


@dataclass
class ConversationContext:
    """Context for a conversation to be processed for memory update."""

    thread_id: str
    messages: list[Any]
    timestamp: datetime = field(default_factory=_now_cn)


class MemoryUpdateQueue:
    """Queue for memory updates with debounce mechanism.

    Each thread_id is debounced independently to avoid cross-thread starvation.
    Newer updates replace older pending updates for the same thread.
    """

    def __init__(self):
        """Initialize the memory update queue."""
        self._pending: dict[str, ConversationContext] = {}
        self._due_at: dict[str, float] = {}
        self._first_seen_at: dict[str, float] = {}
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._processing = False
        self._max_wait_multiplier = 4

    def add(self, thread_id: str, messages: list[Any]) -> None:
        """Add a conversation to the update queue.

        Args:
            thread_id: The thread ID.
            messages: The conversation messages.
        """
        config = get_memory_config()
        if not config.enabled:
            return

        context = ConversationContext(
            thread_id=thread_id,
            messages=messages,
        )

        with self._lock:
            self._enqueue_locked(thread_id, context, config.debounce_seconds)
            self._schedule_locked()
            queue_size = len(self._pending)

        logger.info(
            "event=memory_update_queued thread_id=%s queue_size=%d",
            thread_id,
            queue_size,
        )

    def _enqueue_locked(self, thread_id: str, context: ConversationContext, debounce_seconds: int) -> None:
        now = time.monotonic()
        first_seen = self._first_seen_at.get(thread_id, now)
        self._first_seen_at[thread_id] = first_seen

        max_wait_seconds = debounce_seconds * self._max_wait_multiplier
        due_at = min(
            now + debounce_seconds,
            first_seen + max_wait_seconds,
        )

        self._pending[thread_id] = context
        self._due_at[thread_id] = due_at

    def _schedule_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

        if self._processing or not self._due_at:
            return

        next_due_at = min(self._due_at.values())
        delay = max(0.0, next_due_at - time.monotonic())
        self._timer = threading.Timer(delay, self._process_due)
        self._timer.daemon = True
        self._timer.start()

    def _collect_ready_locked(self, *, force_all: bool) -> list[ConversationContext]:
        if force_all:
            ready_thread_ids = list(self._pending.keys())
        else:
            now = time.monotonic()
            ready_thread_ids = [thread_id for thread_id, due_at in self._due_at.items() if due_at <= now]

        if not ready_thread_ids:
            return []

        contexts: list[ConversationContext] = []
        for thread_id in ready_thread_ids:
            context = self._pending.pop(thread_id, None)
            self._due_at.pop(thread_id, None)
            self._first_seen_at.pop(thread_id, None)
            if context is not None:
                contexts.append(context)
        return contexts

    def _process_contexts(self, contexts_to_process: list[ConversationContext]) -> None:
        from src.agents.memory.updater import MemoryUpdater

        logger.info(
            "event=memory_update_batch_processing batch_size=%d",
            len(contexts_to_process),
        )
        updater = MemoryUpdater()

        for context in contexts_to_process:
            try:
                logger.info(
                    "event=memory_update_started thread_id=%s",
                    context.thread_id,
                )
                success = updater.update_memory(
                    messages=context.messages,
                    thread_id=context.thread_id,
                )
                if success:
                    logger.info(
                        "event=memory_update_succeeded thread_id=%s",
                        context.thread_id,
                    )
                else:
                    logger.warning(
                        "event=memory_update_skipped_or_failed thread_id=%s",
                        context.thread_id,
                    )
            except Exception:
                logger.exception(
                    "event=memory_update_exception thread_id=%s",
                    context.thread_id,
                )

    def _process_due(self) -> None:
        with self._lock:
            if self._processing:
                return

            contexts_to_process = self._collect_ready_locked(force_all=False)
            if not contexts_to_process:
                self._schedule_locked()
                return

            self._processing = True
            self._timer = None

        try:
            self._process_contexts(contexts_to_process)
        finally:
            with self._lock:
                self._processing = False
                self._schedule_locked()

    def flush(self) -> None:
        """Force immediate processing of the queue.

        This is useful for testing or graceful shutdown.
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if self._processing:
                return
            contexts_to_process = self._collect_ready_locked(force_all=True)
            if not contexts_to_process:
                return
            self._processing = True

        try:
            self._process_contexts(contexts_to_process)
        finally:
            with self._lock:
                self._processing = False
                self._schedule_locked()

    def clear(self) -> None:
        """Clear the queue without processing.

        This is useful for testing.
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._pending.clear()
            self._due_at.clear()
            self._first_seen_at.clear()
            self._processing = False

    @property
    def pending_count(self) -> int:
        """Get the number of pending updates."""
        with self._lock:
            return len(self._pending)

    @property
    def is_processing(self) -> bool:
        """Check if the queue is currently being processed."""
        with self._lock:
            return self._processing


# Global singleton instance
_memory_queue: MemoryUpdateQueue | None = None
_queue_lock = threading.Lock()


def get_memory_queue() -> MemoryUpdateQueue:
    """Get the global memory update queue singleton.

    Returns:
        The memory update queue instance.
    """
    global _memory_queue
    with _queue_lock:
        if _memory_queue is None:
            _memory_queue = MemoryUpdateQueue()
        return _memory_queue


def reset_memory_queue() -> None:
    """Reset the global memory queue.

    This is useful for testing.
    """
    global _memory_queue
    with _queue_lock:
        if _memory_queue is not None:
            _memory_queue.clear()
        _memory_queue = None
