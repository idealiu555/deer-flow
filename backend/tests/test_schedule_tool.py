"""Regression tests for schedule tool validation behavior."""

from __future__ import annotations

import asyncio
import importlib
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

from src.config.scheduler_config import SchedulerConfig
from src.scheduler.store import SchedulerStore

schedule_tool_module = importlib.import_module("src.tools.builtins.schedule_tool")


class _DummyStore:
    def __init__(self) -> None:
        self.create_draft_called = False

    def create_draft(self, **kwargs):  # noqa: ANN003 - test stub
        self.create_draft_called = True
        return {"id": "draft-1", **kwargs}


@pytest.mark.parametrize("action", ["update", "remove", "run"])
def test_draft_requires_schedule_id_for_non_add_actions(action: str, monkeypatch) -> None:
    dummy_store = _DummyStore()
    monkeypatch.setattr(schedule_tool_module, "get_scheduler_store", lambda _cfg: dummy_store)

    runtime = SimpleNamespace(context={"thread_id": "thread-test"}, config={})

    payload = asyncio.run(
        schedule_tool_module.schedule_tool.coroutine(
            runtime=runtime,
            action=action,
            confirmed=False,
            schedule_id=None,
        )
    )
    parsed = json.loads(payload)

    assert parsed["success"] is False
    assert parsed["action"] == action
    assert parsed["error"] == "schedule_id is required"
    assert dummy_store.create_draft_called is False


def test_add_persists_json_safe_runtime_snapshot(tmp_path, monkeypatch) -> None:
    store = SchedulerStore(SchedulerConfig(db_path=str(tmp_path / "schedule_tool.db")))

    monkeypatch.setattr(schedule_tool_module, "get_scheduler_store", lambda _cfg: store)
    monkeypatch.setattr(schedule_tool_module, "wake_running_scheduler_service_best_effort", lambda: True)

    runtime = SimpleNamespace(
        context={"thread_id": "thread-test", "assistant_id": "lead_agent"},
        config={
            "recursion_limit": 100,
            "configurable": {
                "model_name": "glm-4.5",
                "thinking_enabled": True,
                "reasoning_effort": "high",
            },
            "metadata": {"trace_id": "abc"},
            "callbacks": object(),  # non-JSON value should be dropped
        },
    )
    schedule_payload = {
        "title": "Daily",
        "prompt": "Do research",
        "kind": "cron",
        "cron": "0 9 * * *",
        "timezone": "Asia/Shanghai",
    }

    add_raw = asyncio.run(
        schedule_tool_module.schedule_tool.coroutine(
            runtime=runtime,
            action="add",
            confirmed=True,
            schedule=schedule_payload,
        )
    )
    add_result = json.loads(add_raw)
    assert add_result["success"] is True
    assert "draft" in add_result
    draft_id = add_result["draft"]["id"]

    confirm_raw = asyncio.run(
        schedule_tool_module.schedule_tool.coroutine(
            runtime=runtime,
            action="confirm",
            draft_id=draft_id,
            confirmed=True,
        )
    )
    confirm_result = json.loads(confirm_raw)
    assert confirm_result["success"] is True
    created = confirm_result["schedule"]
    assert created["config"]["recursion_limit"] == 100
    assert "configurable" not in created["config"]
    assert created["context"]["thread_id"] == "thread-test"
    assert created["context"]["assistant_id"] == "lead_agent"
    assert created["context"]["model_name"] == "glm-4.5"
    assert created["context"]["thinking_enabled"] is True
    assert created["context"]["reasoning_effort"] == "high"
    assert "callbacks" not in created["config"]


def test_add_succeeds_when_no_running_scheduler_service(tmp_path, monkeypatch) -> None:
    store = SchedulerStore(SchedulerConfig(db_path=str(tmp_path / "schedule_tool_not_running.db")))
    wake_calls = 0

    class _FakeService:
        _running = True

        def wake(self) -> None:
            nonlocal wake_calls
            wake_calls += 1

    async def _fake_start_service():
        return _FakeService()

    monkeypatch.setattr(schedule_tool_module, "get_scheduler_store", lambda _cfg: store)
    monkeypatch.setattr(schedule_tool_module, "wake_running_scheduler_service_best_effort", lambda: False)
    monkeypatch.setattr(schedule_tool_module, "start_scheduler_service", _fake_start_service)

    runtime = SimpleNamespace(context={"thread_id": "thread-test", "assistant_id": "lead_agent"}, config={})
    schedule_payload = {
        "title": "Daily",
        "prompt": "Do research",
        "kind": "cron",
        "cron": "0 9 * * *",
        "timezone": "Asia/Shanghai",
    }

    add_raw = asyncio.run(
        schedule_tool_module.schedule_tool.coroutine(
            runtime=runtime,
            action="add",
            confirmed=True,
            schedule=schedule_payload,
        )
    )
    add_result = json.loads(add_raw)
    assert add_result["success"] is True
    assert "draft" in add_result
    assert wake_calls == 0

    confirm_raw = asyncio.run(
        schedule_tool_module.schedule_tool.coroutine(
            runtime=runtime,
            action="confirm",
            draft_id=add_result["draft"]["id"],
            confirmed=True,
        )
    )
    confirm_result = json.loads(confirm_raw)
    assert confirm_result["success"] is True
    assert wake_calls == 1


def test_add_with_owner_key_override_derives_owner_identity(tmp_path, monkeypatch) -> None:
    store = SchedulerStore(SchedulerConfig(db_path=str(tmp_path / "schedule_tool_owner_override.db")))
    wake_calls = 0

    class _FakeService:
        _running = True

        def wake(self) -> None:
            nonlocal wake_calls
            wake_calls += 1

    async def _fake_start_service():
        return _FakeService()

    monkeypatch.setattr(schedule_tool_module, "get_scheduler_store", lambda _cfg: store)
    monkeypatch.setattr(schedule_tool_module, "wake_running_scheduler_service_best_effort", lambda: False)
    monkeypatch.setattr(schedule_tool_module, "start_scheduler_service", _fake_start_service)

    runtime = SimpleNamespace(
        context={
            "thread_id": "thread-test",
            "assistant_id": "lead_agent",
            "channel_name": "telegram",
            "chat_id": "chat-1",
            "user_id": "runtime-user",
        },
        config={},
    )
    schedule_payload = {
        "title": "Daily",
        "prompt": "Do research",
        "kind": "cron",
        "cron": "0 9 * * *",
        "timezone": "Asia/Shanghai",
    }

    add_raw = asyncio.run(
        schedule_tool_module.schedule_tool.coroutine(
            runtime=runtime,
            action="add",
            confirmed=True,
            schedule=schedule_payload,
            query={"owner_key": "web:alice"},
        )
    )
    add_result = json.loads(add_raw)
    assert add_result["success"] is True
    assert "draft" in add_result
    assert wake_calls == 0

    confirm_raw = asyncio.run(
        schedule_tool_module.schedule_tool.coroutine(
            runtime=runtime,
            action="confirm",
            draft_id=add_result["draft"]["id"],
            confirmed=True,
            query={"owner_key": "web:alice"},
        )
    )
    confirm_result = json.loads(confirm_raw)
    assert confirm_result["success"] is True
    created = confirm_result["schedule"]
    assert created["owner_key"] == "web:alice"
    assert created["owner_channel"] == "web"
    assert created["owner_user"] == "alice"
    assert wake_calls == 1


def test_confirm_add_preserves_empty_drafted_runtime_snapshot(tmp_path, monkeypatch) -> None:
    store = SchedulerStore(SchedulerConfig(db_path=str(tmp_path / "schedule_tool_confirm_empty_snapshot.db")))
    wake_calls = 0

    class _FakeService:
        _running = True

        def wake(self) -> None:
            nonlocal wake_calls
            wake_calls += 1

    async def _fake_start_service():
        return _FakeService()

    monkeypatch.setattr(schedule_tool_module, "get_scheduler_store", lambda _cfg: store)
    monkeypatch.setattr(schedule_tool_module, "wake_running_scheduler_service_best_effort", lambda: False)
    monkeypatch.setattr(schedule_tool_module, "start_scheduler_service", _fake_start_service)

    schedule_payload = {
        "title": "Daily",
        "prompt": "Do research",
        "kind": "cron",
        "cron": "0 9 * * *",
        "timezone": "Asia/Shanghai",
    }

    draft_runtime = SimpleNamespace(context={}, config={})
    draft_raw = asyncio.run(
        schedule_tool_module.schedule_tool.coroutine(
            runtime=draft_runtime,
            action="add",
            confirmed=False,
            schedule=schedule_payload,
        )
    )
    draft_result = json.loads(draft_raw)
    assert draft_result["success"] is True
    draft_id = draft_result["draft"]["id"]

    confirm_runtime = SimpleNamespace(
        context={"thread_id": "thread-new", "assistant_id": "lead_agent"},
        config={"recursion_limit": 120, "configurable": {"model_name": "glm-5"}},
    )
    confirm_raw = asyncio.run(
        schedule_tool_module.schedule_tool.coroutine(
            runtime=confirm_runtime,
            action="confirm",
            draft_id=draft_id,
            confirmed=True,
        )
    )
    confirm_result = json.loads(confirm_raw)
    assert confirm_result["success"] is True
    created = confirm_result["schedule"]
    assert created["config"] == {}
    assert created["context"] == {}
    assert wake_calls >= 1


def test_confirm_requires_follow_up_user_confirmation_message(tmp_path, monkeypatch) -> None:
    store = SchedulerStore(SchedulerConfig(db_path=str(tmp_path / "schedule_tool_confirm_follow_up.db")))
    wake_calls = 0

    class _FakeService:
        _running = True

        def wake(self) -> None:
            nonlocal wake_calls
            wake_calls += 1

    async def _fake_start_service():
        return _FakeService()

    monkeypatch.setattr(schedule_tool_module, "get_scheduler_store", lambda _cfg: store)
    monkeypatch.setattr(schedule_tool_module, "wake_running_scheduler_service_best_effort", lambda: False)
    monkeypatch.setattr(schedule_tool_module, "start_scheduler_service", _fake_start_service)

    schedule_payload = {
        "title": "Daily",
        "prompt": "Do research",
        "kind": "cron",
        "cron": "0 9 * * *",
        "timezone": "Asia/Shanghai",
    }
    initial_message = HumanMessage(id="msg-1", content="请每天9点提醒我复盘")
    runtime_add = SimpleNamespace(
        context={"thread_id": "thread-test", "assistant_id": "lead_agent"},
        config={},
        state={"messages": [initial_message]},
    )

    draft_raw = asyncio.run(
        schedule_tool_module.schedule_tool.coroutine(
            runtime=runtime_add,
            action="add",
            confirmed=True,
            schedule=schedule_payload,
        )
    )
    draft_result = json.loads(draft_raw)
    assert draft_result["success"] is True
    draft_id = draft_result["draft"]["id"]

    runtime_same_turn = SimpleNamespace(
        context={"thread_id": "thread-test", "assistant_id": "lead_agent"},
        config={},
        state={"messages": [initial_message]},
    )
    blocked_raw = asyncio.run(
        schedule_tool_module.schedule_tool.coroutine(
            runtime=runtime_same_turn,
            action="confirm",
            draft_id=draft_id,
            confirmed=True,
        )
    )
    blocked_result = json.loads(blocked_raw)
    assert blocked_result["success"] is False
    assert "follow-up user message" in blocked_result["error"]
    assert wake_calls == 0

    runtime_follow_up = SimpleNamespace(
        context={"thread_id": "thread-test", "assistant_id": "lead_agent"},
        config={},
        state={"messages": [initial_message, HumanMessage(id="msg-2", content="1")]},
    )
    confirm_raw = asyncio.run(
        schedule_tool_module.schedule_tool.coroutine(
            runtime=runtime_follow_up,
            action="confirm",
            draft_id=draft_id,
            confirmed=True,
        )
    )
    confirm_result = json.loads(confirm_raw)
    assert confirm_result["success"] is True
    assert confirm_result["confirmed_action"] == "add"
    assert wake_calls == 1


def test_confirm_accepts_any_follow_up_message_when_model_calls_confirm(tmp_path, monkeypatch) -> None:
    store = SchedulerStore(SchedulerConfig(db_path=str(tmp_path / "schedule_tool_confirm_negative.db")))
    wake_calls = 0

    monkeypatch.setattr(schedule_tool_module, "get_scheduler_store", lambda _cfg: store)
    monkeypatch.setattr(schedule_tool_module, "wake_running_scheduler_service_best_effort", lambda: False)

    class _FakeService:
        _running = True

        def wake(self) -> None:
            nonlocal wake_calls
            wake_calls += 1

    async def _fake_start_service():
        return _FakeService()

    monkeypatch.setattr(schedule_tool_module, "start_scheduler_service", _fake_start_service)

    schedule_payload = {
        "title": "Daily",
        "prompt": "Do research",
        "kind": "cron",
        "cron": "0 9 * * *",
        "timezone": "Asia/Shanghai",
    }
    initial_message = HumanMessage(id="msg-1", content="请每天9点提醒我复盘")
    add_runtime = SimpleNamespace(
        context={"thread_id": "thread-test", "assistant_id": "lead_agent"},
        config={},
        state={"messages": [initial_message]},
    )

    draft_raw = asyncio.run(
        schedule_tool_module.schedule_tool.coroutine(
            runtime=add_runtime,
            action="add",
            confirmed=True,
            schedule=schedule_payload,
        )
    )
    draft_result = json.loads(draft_raw)
    assert draft_result["success"] is True
    draft_id = draft_result["draft"]["id"]

    follow_up_runtime = SimpleNamespace(
        context={"thread_id": "thread-test", "assistant_id": "lead_agent"},
        config={},
        state={"messages": [initial_message, HumanMessage(id="msg-2", content="那就按这个方案做")]},
    )
    confirmed_raw = asyncio.run(
        schedule_tool_module.schedule_tool.coroutine(
            runtime=follow_up_runtime,
            action="confirm",
            draft_id=draft_id,
            confirmed=True,
        )
    )
    confirmed_result = json.loads(confirmed_raw)
    assert confirmed_result["success"] is True
    assert confirmed_result["confirmed_action"] == "add"
    assert wake_calls == 1


def test_update_confirm_requires_follow_up_user_message(tmp_path, monkeypatch) -> None:
    store = SchedulerStore(SchedulerConfig(db_path=str(tmp_path / "schedule_tool_update_follow_up.db")))
    wake_calls = 0

    class _FakeService:
        _running = True

        def wake(self) -> None:
            nonlocal wake_calls
            wake_calls += 1

    async def _fake_start_service():
        return _FakeService()

    monkeypatch.setattr(schedule_tool_module, "get_scheduler_store", lambda _cfg: store)
    monkeypatch.setattr(schedule_tool_module, "wake_running_scheduler_service_best_effort", lambda: False)
    monkeypatch.setattr(schedule_tool_module, "start_scheduler_service", _fake_start_service)

    created = store.create_schedule(
        owner_key="web:settings",
        owner_channel="web",
        owner_user="settings",
        channel_name=None,
        chat_id=None,
        topic_id=None,
        thread_id="thread-test",
        assistant_id="lead_agent",
        payload={
            "title": "Daily",
            "prompt": "Do research",
            "kind": "cron",
            "cron": "0 9 * * *",
            "timezone": "Asia/Shanghai",
        },
        config={},
        context={},
    )

    initial_message = HumanMessage(id="msg-1", content="把标题改成每天总结")
    update_runtime = SimpleNamespace(
        context={"thread_id": "thread-test", "assistant_id": "lead_agent"},
        config={},
        state={"messages": [initial_message]},
    )

    draft_raw = asyncio.run(
        schedule_tool_module.schedule_tool.coroutine(
            runtime=update_runtime,
            action="update",
            schedule_id=created["id"],
            schedule={"title": "Daily summary"},
            confirmed=False,
        )
    )
    draft_result = json.loads(draft_raw)
    assert draft_result["success"] is True
    draft_id = draft_result["draft"]["id"]

    blocked_raw = asyncio.run(
        schedule_tool_module.schedule_tool.coroutine(
            runtime=update_runtime,
            action="confirm",
            draft_id=draft_id,
            confirmed=True,
        )
    )
    blocked_result = json.loads(blocked_raw)
    assert blocked_result["success"] is False
    assert "follow-up user message" in blocked_result["error"]
    assert wake_calls == 0

    follow_up_runtime = SimpleNamespace(
        context={"thread_id": "thread-test", "assistant_id": "lead_agent"},
        config={},
        state={"messages": [initial_message, HumanMessage(id="msg-2", content="按这个来")]},
    )
    confirm_raw = asyncio.run(
        schedule_tool_module.schedule_tool.coroutine(
            runtime=follow_up_runtime,
            action="confirm",
            draft_id=draft_id,
            confirmed=True,
        )
    )
    confirm_result = json.loads(confirm_raw)
    assert confirm_result["success"] is True
    assert confirm_result["confirmed_action"] == "update"
    assert confirm_result["schedule"]["title"] == "Daily summary"
    assert wake_calls == 1


def test_update_always_creates_draft_even_when_confirmed_true(tmp_path, monkeypatch) -> None:
    store = SchedulerStore(SchedulerConfig(db_path=str(tmp_path / "schedule_tool_update_always_draft.db")))
    monkeypatch.setattr(schedule_tool_module, "get_scheduler_store", lambda _cfg: store)

    created = store.create_schedule(
        owner_key="web:settings",
        owner_channel="web",
        owner_user="settings",
        channel_name=None,
        chat_id=None,
        topic_id=None,
        thread_id="thread-test",
        assistant_id="lead_agent",
        payload={
            "title": "Daily",
            "prompt": "Do research",
            "kind": "cron",
            "cron": "0 9 * * *",
            "timezone": "Asia/Shanghai",
        },
        config={},
        context={},
    )

    runtime = SimpleNamespace(
        context={"thread_id": "thread-test", "assistant_id": "lead_agent"},
        config={},
        state={"messages": [HumanMessage(id="msg-1", content="帮我把标题改成 Daily summary")]},
    )

    raw = asyncio.run(
        schedule_tool_module.schedule_tool.coroutine(
            runtime=runtime,
            action="update",
            schedule_id=created["id"],
            schedule={"title": "Daily summary"},
            confirmed=True,
        )
    )
    result = json.loads(raw)

    assert result["success"] is True
    assert result["action"] == "update"
    assert "draft" in result
    assert "schedule" not in result

    unchanged = store.get_schedule(schedule_id=created["id"], owner_key="web:settings")
    assert unchanged is not None
    assert unchanged["title"] == "Daily"

def test_list_rejects_non_integer_limit(monkeypatch) -> None:
    class _ListStore:
        def list_schedules(self, **kwargs):  # noqa: ANN003 - test stub
            return []

    monkeypatch.setattr(schedule_tool_module, "get_scheduler_store", lambda _cfg: _ListStore())
    runtime = SimpleNamespace(context={"thread_id": "thread-test"}, config={})

    raw = asyncio.run(
        schedule_tool_module.schedule_tool.coroutine(
            runtime=runtime,
            action="list",
            query={"limit": "all"},
        )
    )
    result = json.loads(raw)
    assert result["success"] is False
    assert result["error"] == "query.limit must be an integer"


def test_wake_reports_when_service_is_not_running(monkeypatch) -> None:
    class _AnyStore:
        pass

    class _FakeService:
        _running = False

        def wake(self) -> None:
            raise AssertionError("wake should not be called when scheduler service is not running")

    async def _fake_start_service():
        return _FakeService()

    monkeypatch.setattr(schedule_tool_module, "get_scheduler_store", lambda _cfg: _AnyStore())
    monkeypatch.setattr(schedule_tool_module, "wake_running_scheduler_service_best_effort", lambda: False)
    monkeypatch.setattr(schedule_tool_module, "start_scheduler_service", _fake_start_service)
    runtime = SimpleNamespace(context={"thread_id": "thread-test"}, config={})

    raw = asyncio.run(
        schedule_tool_module.schedule_tool.coroutine(
            runtime=runtime,
            action="wake",
        )
    )
    result = json.loads(raw)
    assert result["success"] is True
    assert result["message"] == "No running scheduler service to wake"


def test_wake_starts_and_wakes_service_when_absent(monkeypatch) -> None:
    class _AnyStore:
        pass

    wake_calls = 0

    class _FakeService:
        _running = True

        def wake(self) -> None:
            nonlocal wake_calls
            wake_calls += 1

    async def _fake_start_service():
        return _FakeService()

    monkeypatch.setattr(schedule_tool_module, "get_scheduler_store", lambda _cfg: _AnyStore())
    monkeypatch.setattr(schedule_tool_module, "wake_running_scheduler_service_best_effort", lambda: False)
    monkeypatch.setattr(schedule_tool_module, "start_scheduler_service", _fake_start_service)
    runtime = SimpleNamespace(context={"thread_id": "thread-test"}, config={})

    raw = asyncio.run(
        schedule_tool_module.schedule_tool.coroutine(
            runtime=runtime,
            action="wake",
        )
    )
    result = json.loads(raw)
    assert result["success"] is True
    assert result["message"] == "Scheduler loop awakened"
    assert wake_calls == 1
