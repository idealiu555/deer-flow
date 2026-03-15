"""Background scheduler service."""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

from app.channels.manager import _extract_artifacts, _extract_response_text, _format_artifact_text, _resolve_attachments
from app.channels.message_bus import OutboundMessage
from deerflow.config.app_config import get_app_config
from deerflow.config.scheduler_config import SchedulerConfig, get_scheduler_config
from deerflow.scheduler.store import SchedulerStore, get_scheduler_store

logger = logging.getLogger(__name__)

_DEFAULT_LANGGRAPH_URL_LOCAL = "http://localhost:2024"
_DEFAULT_LANGGRAPH_URL_CONTAINER = "http://langgraph:2024"
_DEFAULT_ASSISTANT_ID = "lead_agent"


def _is_container_runtime() -> bool:
    return Path("/.dockerenv").is_file()


def _default_langgraph_url() -> str:
    if _is_container_runtime():
        return _DEFAULT_LANGGRAPH_URL_CONTAINER
    return _DEFAULT_LANGGRAPH_URL_LOCAL


def _normalize_run_params_for_langgraph(run_config: dict[str, Any], run_context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Normalize scheduler run inputs for LangGraph API compatibility."""
    normalized_config = dict(run_config or {})
    normalized_context = dict(run_context or {})
    configurable = normalized_config.pop("configurable", None)
    if isinstance(configurable, dict):
        for key, value in configurable.items():
            normalized_context.setdefault(str(key), value)
    return normalized_config, normalized_context


class SchedulerService:
    """Periodic dispatcher for persisted schedules."""

    def __init__(self, *, config: SchedulerConfig, store: SchedulerStore, langgraph_url: str, default_assistant_id: str):
        self._config = config
        self._store = store
        self._langgraph_url = langgraph_url
        self._default_assistant_id = default_assistant_id
        self._instance_id = f"scheduler-{uuid.uuid4().hex[:8]}"
        self._running = False
        self._task: asyncio.Task | None = None
        self._wake_event = asyncio.Event()
        self._inflight_tasks: set[asyncio.Task[Any]] = set()
        self._client = None

    def _get_client(self):
        if self._client is None:
            from langgraph_sdk import get_client

            self._client = get_client(url=self._langgraph_url)
        return self._client

    async def start(self) -> None:
        if self._running or not self._config.enabled:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="scheduler-service-loop")
        logger.info("SchedulerService started (poll=%ss, concurrency=%s)", self._config.poll_interval_seconds, self._config.max_concurrency)

    async def stop(self) -> None:
        self._running = False
        self.wake()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._inflight_tasks:
            tasks = tuple(self._inflight_tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._inflight_tasks.clear()
        logger.info("SchedulerService stopped")

    def wake(self) -> None:
        self._wake_event.set()

    def _on_inflight_done(self, task: asyncio.Task[Any]) -> None:
        self._inflight_tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Claimed scheduler task failed unexpectedly")
        self.wake()

    def _dispatch_claimed(self, claimed: list[dict[str, Any]]) -> None:
        for schedule in claimed:
            schedule_id = str(schedule.get("id") or "")
            task = asyncio.create_task(self._run_claimed(schedule), name=f"scheduler-run-{schedule_id}")
            self._inflight_tasks.add(task)
            task.add_done_callback(self._on_inflight_done)

    @staticmethod
    def _channel_delivery_available() -> bool:
        try:
            from app.channels.service import get_channel_service
        except Exception:
            return False
        return get_channel_service() is not None

    async def _loop(self) -> None:
        while self._running:
            try:
                available_slots = self._config.max_concurrency - len(self._inflight_tasks)
                if available_slots > 0:
                    claimed = self._store.claim_due_schedules(
                        limit=available_slots,
                        lease_owner=self._instance_id,
                        lease_seconds=self._config.lease_seconds,
                        include_channel_targets=self._channel_delivery_available(),
                    )
                    if claimed:
                        self._dispatch_claimed(claimed)
                        continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduler loop iteration failed")

            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=self._config.poll_interval_seconds)
            except TimeoutError:
                pass
            finally:
                self._wake_event.clear()

    async def _run_claimed(self, schedule: dict[str, Any]) -> None:
        schedule_id = str(schedule.get("id") or "")
        planned_at = schedule.get("next_run_at")
        max_attempts = 1 + max(0, self._config.retry_attempts)
        success = False
        last_error: str | None = None
        heartbeat_task = asyncio.create_task(self._lease_heartbeat(schedule_id), name=f"scheduler-lease-{schedule_id}")
        try:
            for attempt in range(1, max_attempts + 1):
                run = self._store.create_run(schedule_id=schedule_id, planned_at=planned_at, attempt=attempt)
                run_id = str(run.get("id") or "")
                try:
                    output = await self._execute_schedule(schedule)
                    self._store.finish_run(run_id=run_id, status="success", output=output)
                    success = True
                    last_error = None
                    break
                except asyncio.CancelledError:
                    last_error = "Scheduled execution cancelled"
                    self._store.finish_run(run_id=run_id, status="failed", error=last_error)
                    raise
                except Exception as exc:
                    last_error = str(exc)
                    logger.exception("Scheduled execution failed: schedule_id=%s attempt=%s", schedule_id, attempt)
                    self._store.finish_run(run_id=run_id, status="failed", error=last_error)
        finally:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
            self._store.release_schedule_claim(
                schedule_id=schedule_id,
                lease_owner=self._instance_id,
                success=success,
                error=last_error,
            )

    async def _lease_heartbeat(self, schedule_id: str) -> None:
        interval_seconds = max(1, self._config.lease_seconds // 3)
        while True:
            await asyncio.sleep(interval_seconds)
            renewed = self._store.renew_schedule_lease(
                schedule_id=schedule_id,
                lease_owner=self._instance_id,
                lease_seconds=self._config.lease_seconds,
            )
            if not renewed:
                logger.warning("Lease renewal skipped (claim lost): schedule_id=%s instance=%s", schedule_id, self._instance_id)
                return

    async def _execute_schedule(self, schedule: dict[str, Any]) -> str:
        client = self._get_client()
        thread_id = str(schedule.get("thread_id") or "").strip()
        if not thread_id:
            thread = await client.threads.create()
            thread_id = str(thread["thread_id"])
            self._store.set_schedule_thread(schedule_id=schedule["id"], thread_id=thread_id)

        assistant_id = str(schedule.get("assistant_id") or self._default_assistant_id)

        run_config = dict(schedule.get("config") or {})
        run_context = dict(schedule.get("context") or {})
        run_context.update(
            {
                "thread_id": thread_id,
                "channel_name": schedule.get("channel_name"),
                "chat_id": schedule.get("chat_id"),
                "topic_id": schedule.get("topic_id"),
                "assistant_id": assistant_id,
            }
        )
        owner_user = schedule.get("owner_user")
        if owner_user is not None:
            run_context["user_id"] = owner_user
        run_config, run_context = _normalize_run_params_for_langgraph(run_config, run_context)

        run_kwargs: dict[str, Any] = {}
        if run_config:
            run_kwargs["config"] = run_config
        if run_context:
            run_kwargs["context"] = run_context
        result = await client.runs.wait(
            thread_id,
            assistant_id,
            input={"messages": [{"role": "human", "content": schedule["prompt"]}]},
            **run_kwargs,
        )

        response_text = _extract_response_text(result)
        artifacts = _extract_artifacts(result)

        if artifacts and not response_text:
            response_text = _format_artifact_text(artifacts)

        if not response_text:
            response_text = "(No response from scheduled task)"

        await self._deliver_outbound(schedule=schedule, thread_id=thread_id, response_text=response_text, artifacts=artifacts)
        return response_text

    async def _deliver_outbound(self, *, schedule: dict[str, Any], thread_id: str, response_text: str, artifacts: list[str]) -> None:
        channel_name = str(schedule.get("channel_name") or "").strip()
        chat_id = str(schedule.get("chat_id") or "").strip()
        if not channel_name or not chat_id:
            return

        attachments = _resolve_attachments(thread_id, artifacts)
        if attachments:
            attachment_text = _format_artifact_text([a.virtual_path for a in attachments])
            response_text = f"{response_text}\n\n{attachment_text}"

        from app.channels.service import get_channel_service

        channel_service = get_channel_service()
        if channel_service is None:
            raise RuntimeError("Channel service is not running")

        outbound = OutboundMessage(
            channel_name=channel_name,
            chat_id=chat_id,
            thread_id=thread_id,
            text=response_text,
            artifacts=artifacts,
            attachments=attachments,
            thread_ts=str(schedule.get("topic_id") or "").strip() or None,
        )
        await channel_service.bus.publish_outbound(outbound)


def _resolve_scheduler_runtime() -> tuple[SchedulerConfig, str, str]:
    config = get_scheduler_config() or SchedulerConfig()

    langgraph_url = _default_langgraph_url()
    assistant_id = _DEFAULT_ASSISTANT_ID
    try:
        app_config = get_app_config()
        channels = (app_config.model_extra or {}).get("channels")
        if isinstance(channels, dict):
            langgraph_url = str(channels.get("langgraph_url") or langgraph_url)
            session = channels.get("session")
            if isinstance(session, dict):
                assistant_id = str(session.get("assistant_id") or assistant_id)
    except Exception:
        logger.exception("Failed to resolve scheduler runtime from app config; using defaults")

    return config, langgraph_url, assistant_id


_scheduler_service: SchedulerService | None = None
_scheduler_service_key: tuple[Any, ...] | None = None
_scheduler_lock = asyncio.Lock()


def _scheduler_config_key(config: SchedulerConfig) -> tuple[Any, ...]:
    return (
        config.enabled,
        config.db_path,
        config.poll_interval_seconds,
        config.max_concurrency,
        config.lease_seconds,
        config.draft_ttl_seconds,
        config.max_runs_per_schedule,
        config.retry_attempts,
        config.default_timezone,
    )


async def get_scheduler_service() -> SchedulerService:
    """Get scheduler singleton without changing runtime state."""
    global _scheduler_service, _scheduler_service_key
    async with _scheduler_lock:
        cfg, langgraph_url, assistant_id = _resolve_scheduler_runtime()
        key = (*_scheduler_config_key(cfg), langgraph_url, assistant_id)

        if _scheduler_service is not None and _scheduler_service_key == key:
            return _scheduler_service

        if _scheduler_service is not None:
            await _scheduler_service.stop()

        store = get_scheduler_store(cfg)
        _scheduler_service = SchedulerService(
            config=cfg,
            store=store,
            langgraph_url=langgraph_url,
            default_assistant_id=assistant_id,
        )
        _scheduler_service_key = key
    return _scheduler_service


async def start_scheduler_service() -> SchedulerService:
    """Ensure scheduler singleton exists and starts."""
    service = await get_scheduler_service()
    await service.start()
    return service


def wake_running_scheduler_service_best_effort() -> bool:
    """Wake scheduler loop if singleton is already running on any loop."""
    service = _scheduler_service
    if service is None or not bool(getattr(service, "_running", False)):
        return False

    task = getattr(service, "_task", None)
    if task is None:
        return False

    try:
        task.get_loop().call_soon_threadsafe(service.wake)
    except RuntimeError:
        return False
    return True


async def stop_scheduler_service() -> None:
    """Stop scheduler singleton if running."""
    global _scheduler_service, _scheduler_service_key
    async with _scheduler_lock:
        if _scheduler_service is not None:
            await _scheduler_service.stop()
            _scheduler_service = None
        _scheduler_service_key = None
