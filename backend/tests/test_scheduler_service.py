"""Scheduler service tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import deerflow.scheduler.service as scheduler_service_module
from deerflow.config.scheduler_config import SchedulerConfig
from deerflow.scheduler.service import SchedulerService, _normalize_run_params_for_langgraph
from deerflow.scheduler.store import SchedulerStore


def _make_store(tmp_path: Path) -> tuple[SchedulerConfig, SchedulerStore]:
    cfg = SchedulerConfig(
        db_path=str(tmp_path / "scheduler-service.db"),
        poll_interval_seconds=1,
        max_concurrency=1,
        lease_seconds=10,
        draft_ttl_seconds=3600,
        max_runs_per_schedule=10,
        retry_attempts=0,
        default_timezone="Asia/Shanghai",
    )
    return cfg, SchedulerStore(cfg)


def test_normalize_run_params_moves_configurable_to_context() -> None:
    run_config = {"configurable": {"model_name": "glm-5", "thread_id": "from-config"}, "recursion_limit": 120}
    run_context = {"thread_id": "from-context", "channel_name": "web"}
    normalized_config, normalized_context = _normalize_run_params_for_langgraph(run_config, run_context)

    assert normalized_config == {"recursion_limit": 120}
    assert "configurable" in run_config
    assert normalized_context["thread_id"] == "from-context"
    assert normalized_context["channel_name"] == "web"
    assert normalized_context["model_name"] == "glm-5"


def test_default_langgraph_url_is_container_aware(monkeypatch) -> None:
    monkeypatch.setattr(scheduler_service_module, "_is_container_runtime", lambda: True)
    assert scheduler_service_module._default_langgraph_url() == "http://langgraph:2024"

    monkeypatch.setattr(scheduler_service_module, "_is_container_runtime", lambda: False)
    assert scheduler_service_module._default_langgraph_url() == "http://localhost:2024"


@pytest.mark.anyio
async def test_run_claimed_renews_lease_while_running(tmp_path: Path, monkeypatch) -> None:
    cfg, store = _make_store(tmp_path)
    service = SchedulerService(
        config=cfg,
        store=store,
        langgraph_url="http://localhost:2024",
        default_assistant_id="lead_agent",
    )

    created = store.create_schedule(
        owner_key="web:test",
        owner_channel="web",
        owner_user="u1",
        channel_name=None,
        chat_id=None,
        topic_id=None,
        thread_id="t1",
        assistant_id="lead_agent",
        payload={
            "title": "Daily research",
            "prompt": "Research papers about XXX and summarize.",
            "kind": "cron",
            "cron": "*/5 * * * *",
            "timezone": "Asia/Shanghai",
        },
        config={},
        context={},
    )
    schedule_id = created["id"]
    store.trigger_schedule(schedule_id=schedule_id, owner_key="web:test")
    claimed = store.claim_due_schedules(limit=1, lease_owner=service._instance_id, lease_seconds=cfg.lease_seconds)
    assert [item["id"] for item in claimed] == [schedule_id]

    renew_calls = 0
    original_renew = store.renew_schedule_lease

    def _renew_and_count(*, schedule_id: str, lease_owner: str, lease_seconds: int) -> bool:
        nonlocal renew_calls
        renew_calls += 1
        return original_renew(schedule_id=schedule_id, lease_owner=lease_owner, lease_seconds=lease_seconds)

    async def _fake_execute(_schedule: dict) -> str:
        await asyncio.sleep(3.2)
        return "ok"

    monkeypatch.setattr(store, "renew_schedule_lease", _renew_and_count)
    monkeypatch.setattr(service, "_execute_schedule", _fake_execute)

    await service._run_claimed(claimed[0])

    assert renew_calls >= 1
    runs = store.list_runs(schedule_id=schedule_id, owner_key="web:test", limit=5)
    assert runs
    assert runs[0]["status"] == "success"


@pytest.mark.anyio
async def test_run_claimed_marks_cancelled_execution_failed(tmp_path: Path, monkeypatch) -> None:
    cfg, store = _make_store(tmp_path)
    service = SchedulerService(
        config=cfg,
        store=store,
        langgraph_url="http://localhost:2024",
        default_assistant_id="lead_agent",
    )

    created = store.create_schedule(
        owner_key="web:test",
        owner_channel="web",
        owner_user="u1",
        channel_name=None,
        chat_id=None,
        topic_id=None,
        thread_id="t1",
        assistant_id="lead_agent",
        payload={
            "title": "Daily research",
            "prompt": "Research papers about XXX and summarize.",
            "kind": "cron",
            "cron": "*/5 * * * *",
            "timezone": "Asia/Shanghai",
        },
        config={},
        context={},
    )
    schedule_id = created["id"]
    store.trigger_schedule(schedule_id=schedule_id, owner_key="web:test")
    claimed = store.claim_due_schedules(limit=1, lease_owner=service._instance_id, lease_seconds=cfg.lease_seconds)
    assert [item["id"] for item in claimed] == [schedule_id]

    async def _cancelled_execute(_schedule: dict) -> str:
        raise asyncio.CancelledError()

    monkeypatch.setattr(service, "_execute_schedule", _cancelled_execute)

    with pytest.raises(asyncio.CancelledError):
        await service._run_claimed(claimed[0])

    runs = store.list_runs(schedule_id=schedule_id, owner_key="web:test", limit=5)
    assert runs
    assert runs[0]["status"] == "failed"
    assert "cancelled" in str(runs[0]["error"] or "").lower()

    updated = store.get_schedule(schedule_id=schedule_id, owner_key="web:test")
    assert updated is not None
    assert "cancelled" in str(updated["last_error"] or "").lower()


@pytest.mark.anyio
async def test_loop_skips_wait_after_claimed_batch(tmp_path: Path, monkeypatch) -> None:
    cfg, store = _make_store(tmp_path)
    service = SchedulerService(
        config=cfg,
        store=store,
        langgraph_url="http://localhost:2024",
        default_assistant_id="lead_agent",
    )
    service._running = True

    created = store.create_schedule(
        owner_key="web:test",
        owner_channel="web",
        owner_user="u1",
        channel_name=None,
        chat_id=None,
        topic_id=None,
        thread_id="t1",
        assistant_id="lead_agent",
        payload={
            "title": "Daily research",
            "prompt": "Research papers about XXX and summarize.",
            "kind": "cron",
            "cron": "*/5 * * * *",
            "timezone": "Asia/Shanghai",
        },
        config={},
        context={},
    )
    schedule_id = created["id"]
    store.trigger_schedule(schedule_id=schedule_id, owner_key="web:test")

    first_claim = True
    original_claim = store.claim_due_schedules

    def _claim_then_cancel(*, limit: int, lease_owner: str, lease_seconds: int, include_channel_targets: bool):  # noqa: ANN001
        nonlocal first_claim
        if first_claim:
            first_claim = False
            return original_claim(
                limit=limit,
                lease_owner=lease_owner,
                lease_seconds=lease_seconds,
                include_channel_targets=include_channel_targets,
            )
        raise asyncio.CancelledError()

    async def _fast_run_claimed(_schedule: dict) -> None:
        return None

    monkeypatch.setattr(store, "claim_due_schedules", _claim_then_cancel)
    monkeypatch.setattr(service, "_run_claimed", _fast_run_claimed)

    with pytest.raises(asyncio.CancelledError):
        await service._loop()

    assert first_claim is False


@pytest.mark.anyio
async def test_loop_dispatches_new_claim_when_slot_frees(tmp_path: Path, monkeypatch) -> None:
    cfg, store = _make_store(tmp_path)
    cfg.max_concurrency = 2
    service = SchedulerService(
        config=cfg,
        store=store,
        langgraph_url="http://localhost:2024",
        default_assistant_id="lead_agent",
    )
    service._running = True

    short_finished = asyncio.Event()
    long_released = asyncio.Event()
    third_started = asyncio.Event()
    claim_calls = 0

    def _claim_due(*, limit: int, lease_owner: str, lease_seconds: int, include_channel_targets: bool):  # noqa: ANN001
        nonlocal claim_calls
        claim_calls += 1
        if claim_calls == 1:
            assert limit == 2
            return [{"id": "slow"}, {"id": "fast"}]
        if claim_calls == 2:
            assert limit == 1
            assert short_finished.is_set()
            assert not long_released.is_set()
            return [{"id": "third"}]
        return []

    async def _fake_run_claimed(schedule: dict) -> None:
        schedule_id = schedule["id"]
        if schedule_id == "slow":
            await long_released.wait()
            return
        if schedule_id == "fast":
            short_finished.set()
            return
        if schedule_id == "third":
            third_started.set()
            long_released.set()
            service._running = False
            service.wake()

    monkeypatch.setattr(store, "claim_due_schedules", _claim_due)
    monkeypatch.setattr(service, "_run_claimed", _fake_run_claimed)

    await service._loop()

    assert claim_calls >= 2
    assert short_finished.is_set()
    assert third_started.is_set()


@pytest.mark.anyio
async def test_loop_passes_channel_delivery_capability_to_claim(tmp_path: Path, monkeypatch) -> None:
    cfg, store = _make_store(tmp_path)
    service = SchedulerService(
        config=cfg,
        store=store,
        langgraph_url="http://localhost:2024",
        default_assistant_id="lead_agent",
    )
    service._running = True

    captured_include_flag: list[bool] = []

    def _claim_due(*, limit: int, lease_owner: str, lease_seconds: int, include_channel_targets: bool):  # noqa: ANN001
        captured_include_flag.append(include_channel_targets)
        service._running = False
        service.wake()
        return []

    monkeypatch.setattr(store, "claim_due_schedules", _claim_due)
    monkeypatch.setattr(service, "_channel_delivery_available", lambda: False)

    await service._loop()

    assert captured_include_flag == [False]


@pytest.mark.anyio
async def test_execute_schedule_preserves_context_user_id_when_owner_user_missing(tmp_path: Path, monkeypatch) -> None:
    cfg, store = _make_store(tmp_path)
    service = SchedulerService(
        config=cfg,
        store=store,
        langgraph_url="http://localhost:2024",
        default_assistant_id="lead_agent",
    )

    captured: dict[str, object] = {}

    class _Runs:
        async def wait(self, thread_id: str, assistant_id: str, input: dict, **kwargs):  # noqa: A002
            captured["thread_id"] = thread_id
            captured["assistant_id"] = assistant_id
            captured["input"] = input
            captured["kwargs"] = kwargs
            return {"ok": True}

    class _Client:
        runs = _Runs()

    async def _fake_deliver_outbound(*, schedule: dict, thread_id: str, response_text: str, artifacts: list[str]) -> None:
        return None

    monkeypatch.setattr(service, "_get_client", lambda: _Client())
    monkeypatch.setattr(service, "_deliver_outbound", _fake_deliver_outbound)
    monkeypatch.setattr(scheduler_service_module, "_extract_response_text", lambda _result: "ok")
    monkeypatch.setattr(scheduler_service_module, "_extract_artifacts", lambda _result: [])

    response = await service._execute_schedule(
        {
            "id": "sched-1",
            "thread_id": "thread-1",
            "assistant_id": "lead_agent",
            "prompt": "hello",
            "config": {"recursion_limit": 120, "configurable": {"model_name": "glm-5"}},
            "context": {"user_id": "ctx-user"},
            "owner_user": None,
            "channel_name": None,
            "chat_id": None,
            "topic_id": None,
        }
    )

    assert response == "ok"
    assert captured["thread_id"] == "thread-1"
    assert captured["assistant_id"] == "lead_agent"
    run_kwargs = captured["kwargs"]
    assert isinstance(run_kwargs, dict)
    assert run_kwargs["config"] == {"recursion_limit": 120}
    assert run_kwargs["context"]["user_id"] == "ctx-user"
    assert run_kwargs["context"]["model_name"] == "glm-5"


@pytest.mark.anyio
async def test_run_claimed_fails_when_channel_delivery_unavailable(tmp_path: Path, monkeypatch) -> None:
    cfg, store = _make_store(tmp_path)
    service = SchedulerService(
        config=cfg,
        store=store,
        langgraph_url="http://localhost:2024",
        default_assistant_id="lead_agent",
    )

    created = store.create_schedule(
        owner_key="telegram:u1",
        owner_channel="telegram",
        owner_user="u1",
        channel_name="telegram",
        chat_id="chat-1",
        topic_id=None,
        thread_id="thread-1",
        assistant_id="lead_agent",
        payload={
            "title": "Push update",
            "prompt": "send update",
            "kind": "cron",
            "cron": "*/5 * * * *",
            "timezone": "Asia/Shanghai",
        },
        config={},
        context={},
    )
    schedule_id = created["id"]
    store.trigger_schedule(schedule_id=schedule_id, owner_key="telegram:u1")
    claimed = store.claim_due_schedules(limit=1, lease_owner=service._instance_id, lease_seconds=cfg.lease_seconds)
    assert [item["id"] for item in claimed] == [schedule_id]

    class _Runs:
        async def wait(self, thread_id: str, assistant_id: str, input: dict, **kwargs):  # noqa: A002
            return {"ok": True}

    class _Client:
        runs = _Runs()

    monkeypatch.setattr(service, "_get_client", lambda: _Client())
    monkeypatch.setattr(scheduler_service_module, "_extract_response_text", lambda _result: "ok")
    monkeypatch.setattr(scheduler_service_module, "_extract_artifacts", lambda _result: [])
    monkeypatch.setattr("app.channels.service.get_channel_service", lambda: None)

    await service._run_claimed(claimed[0])

    runs = store.list_runs(schedule_id=schedule_id, owner_key="telegram:u1", limit=5)
    assert runs
    assert runs[0]["status"] == "failed"
    assert "Channel service is not running" in str(runs[0]["error"] or "")
    updated = store.get_schedule(schedule_id=schedule_id, owner_key="telegram:u1")
    assert updated is not None
    assert "Channel service is not running" in str(updated["last_error"] or "")


@pytest.mark.anyio
async def test_get_scheduler_service_recreates_when_runtime_changes(tmp_path: Path, monkeypatch) -> None:
    cfg_disabled = SchedulerConfig(enabled=False, db_path=str(tmp_path / "scheduler-disabled.db"))
    cfg_enabled = SchedulerConfig(enabled=True, db_path=str(tmp_path / "scheduler-enabled.db"))
    store_disabled = object()
    store_enabled = object()

    call_count = 0

    def _fake_resolve_runtime() -> tuple[SchedulerConfig, str, str]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return cfg_disabled, "http://localhost:2024", "lead_agent"
        return cfg_enabled, "http://localhost:2030", "next_agent"

    def _fake_get_scheduler_store(config: SchedulerConfig):
        return store_enabled if config.enabled else store_disabled

    monkeypatch.setattr(scheduler_service_module, "_resolve_scheduler_runtime", _fake_resolve_runtime)
    monkeypatch.setattr(scheduler_service_module, "get_scheduler_store", _fake_get_scheduler_store)
    monkeypatch.setattr(scheduler_service_module, "_scheduler_service", None)
    monkeypatch.setattr(scheduler_service_module, "_scheduler_service_key", None)

    first = await scheduler_service_module.get_scheduler_service()
    second = await scheduler_service_module.get_scheduler_service()
    third = await scheduler_service_module.get_scheduler_service()

    assert first is not second
    assert second is third
    assert first._config.enabled is False
    assert second._config.enabled is True
    assert second._langgraph_url == "http://localhost:2030"
    assert second._default_assistant_id == "next_agent"
    assert second._store is store_enabled


@pytest.mark.anyio
async def test_deliver_outbound_uses_topic_id_as_thread_ts(tmp_path: Path, monkeypatch) -> None:
    cfg, store = _make_store(tmp_path)
    service = SchedulerService(
        config=cfg,
        store=store,
        langgraph_url="http://localhost:2024",
        default_assistant_id="lead_agent",
    )

    published: dict[str, object] = {}

    class _Bus:
        async def publish_outbound(self, msg) -> None:  # noqa: ANN001
            published["msg"] = msg

    class _ChannelService:
        bus = _Bus()

    monkeypatch.setattr("app.channels.service.get_channel_service", lambda: _ChannelService())

    await service._deliver_outbound(
        schedule={"channel_name": "feishu", "chat_id": "chat-1", "topic_id": "root-msg-123"},
        thread_id="thread-1",
        response_text="scheduled reply",
        artifacts=[],
    )

    outbound = published["msg"]
    assert outbound is not None
    assert outbound.thread_ts == "root-msg-123"


def test_wake_running_scheduler_service_best_effort_uses_task_loop(monkeypatch) -> None:
    wake_calls = 0
    call_soon_calls = 0

    class _FakeLoop:
        def call_soon_threadsafe(self, cb):  # noqa: ANN001
            nonlocal call_soon_calls
            call_soon_calls += 1
            cb()

    class _FakeTask:
        def get_loop(self):  # noqa: ANN201
            return _FakeLoop()

    class _FakeService:
        _running = True
        _task = _FakeTask()

        def wake(self) -> None:
            nonlocal wake_calls
            wake_calls += 1

    monkeypatch.setattr(scheduler_service_module, "_scheduler_service", _FakeService())

    assert scheduler_service_module.wake_running_scheduler_service_best_effort() is True
    assert call_soon_calls == 1
    assert wake_calls == 1
