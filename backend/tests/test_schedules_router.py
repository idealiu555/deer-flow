"""Schedules router tests."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config.scheduler_config import SchedulerConfig
from src.gateway.routers import schedules as schedules_router
from src.scheduler.store import SchedulerStore

gateway_app = importlib.import_module("src.gateway.app")


class _FakeSchedulerService:
    def __init__(self) -> None:
        self._running = True
        self.wake_calls = 0

    def wake(self) -> None:
        self.wake_calls += 1


def _make_store(tmp_path: Path) -> SchedulerStore:
    cfg = SchedulerConfig(
        db_path=str(tmp_path / "router-scheduler.db"),
        poll_interval_seconds=1,
        max_concurrency=1,
        lease_seconds=30,
        draft_ttl_seconds=3600,
        max_runs_per_schedule=10,
        retry_attempts=1,
        default_timezone="Asia/Shanghai",
    )
    return SchedulerStore(cfg)


def test_schedule_reads_and_mutations_require_owner_key(tmp_path: Path, monkeypatch) -> None:
    store = _make_store(tmp_path)
    fake_service = _FakeSchedulerService()

    async def _fake_get_service():
        return fake_service

    monkeypatch.setattr(schedules_router, "_store", lambda: store)
    monkeypatch.setattr(schedules_router, "get_scheduler_service", _fake_get_service)
    monkeypatch.setattr(schedules_router, "start_scheduler_service", _fake_get_service)

    app = FastAPI()
    app.include_router(schedules_router.router)
    with TestClient(app) as client:
        create_missing_owner = client.post(
            "/api/schedules",
            json={
                "schedule": {
                    "title": "Daily papers",
                    "prompt": "research xxx papers",
                    "kind": "cron",
                    "cron": "0 9 * * *",
                    "timezone": "Asia/Shanghai",
                },
                "confirmed": True,
            },
        )
        assert create_missing_owner.status_code == 422

        create_resp = client.post(
            "/api/schedules",
            json={
                "owner_key": "web:settings",
                "schedule": {
                    "title": "Daily papers",
                    "prompt": "research xxx papers",
                    "kind": "cron",
                    "cron": "0 9 * * *",
                    "timezone": "Asia/Shanghai",
                },
                "confirmed": True,
            },
        )
        assert create_resp.status_code == 200
        schedule_id = create_resp.json()["data"]["schedule"]["id"]

        list_resp = client.get("/api/schedules")
        assert list_resp.status_code == 422

        runs_resp = client.get(f"/api/schedules/{schedule_id}/runs")
        assert runs_resp.status_code == 422

        update_resp = client.patch(f"/api/schedules/{schedule_id}", json={"title": "Daily v2"})
        assert update_resp.status_code == 422

        pause_resp = client.post(f"/api/schedules/{schedule_id}/pause", json={})
        assert pause_resp.status_code == 422

        trigger_resp = client.post(f"/api/schedules/{schedule_id}/trigger", json={"confirmed": True})
        assert trigger_resp.status_code == 422

        delete_resp = client.delete(f"/api/schedules/{schedule_id}?confirmed=true")
        assert delete_resp.status_code == 422


def test_create_derives_owner_channel_and_user_from_owner_key(tmp_path: Path, monkeypatch) -> None:
    store = _make_store(tmp_path)
    fake_service = _FakeSchedulerService()

    async def _fake_get_service():
        return fake_service

    monkeypatch.setattr(schedules_router, "_store", lambda: store)
    monkeypatch.setattr(schedules_router, "get_scheduler_service", _fake_get_service)
    monkeypatch.setattr(schedules_router, "start_scheduler_service", _fake_get_service)

    app = FastAPI()
    app.include_router(schedules_router.router)
    with TestClient(app) as client:
        create_resp = client.post(
            "/api/schedules",
            json={
                "owner_key": "telegram:user-123",
                "owner_channel": "web",
                "owner_user": "other-user",
                "schedule": {
                    "title": "Daily papers",
                    "prompt": "research xxx papers",
                    "kind": "cron",
                    "cron": "0 9 * * *",
                    "timezone": "Asia/Shanghai",
                },
                "confirmed": True,
            },
        )
        assert create_resp.status_code == 200
        created = create_resp.json()["data"]["schedule"]
        assert created["owner_key"] == "telegram:user-123"
        assert created["owner_channel"] == "telegram"
        assert created["owner_user"] == "user-123"


def test_create_defaults_to_draft_when_confirmed_is_omitted(tmp_path: Path, monkeypatch) -> None:
    store = _make_store(tmp_path)
    fake_service = _FakeSchedulerService()

    async def _fake_get_service():
        return fake_service

    monkeypatch.setattr(schedules_router, "_store", lambda: store)
    monkeypatch.setattr(schedules_router, "get_scheduler_service", _fake_get_service)
    monkeypatch.setattr(schedules_router, "start_scheduler_service", _fake_get_service)

    app = FastAPI()
    app.include_router(schedules_router.router)
    with TestClient(app) as client:
        draft_resp = client.post(
            "/api/schedules",
            json={
                "owner_key": "web:settings",
                "schedule": {
                    "title": "Daily papers",
                    "prompt": "research xxx papers",
                    "kind": "cron",
                    "cron": "0 9 * * *",
                    "timezone": "Asia/Shanghai",
                },
            },
        )
        assert draft_resp.status_code == 200
        data = draft_resp.json()["data"]
        assert data["draft_id"] == data["draft"]["id"]
        assert "draft" in data
        assert "schedule" not in data
        assert fake_service.wake_calls == 0

        confirm_resp = client.post(
            f"/api/schedules/drafts/{data['draft']['id']}/confirm",
            json={"owner_key": "web:settings"},
        )
        assert confirm_resp.status_code == 200
        assert confirm_resp.json()["data"]["action"] == "add"
        confirmed = confirm_resp.json()["data"]["schedule"]
        assert confirmed["owner_key"] == "web:settings"
        assert fake_service.wake_calls == 1


def test_mutation_draft_responses_include_top_level_draft_id(tmp_path: Path, monkeypatch) -> None:
    store = _make_store(tmp_path)
    fake_service = _FakeSchedulerService()

    async def _fake_get_service():
        return fake_service

    monkeypatch.setattr(schedules_router, "_store", lambda: store)
    monkeypatch.setattr(schedules_router, "get_scheduler_service", _fake_get_service)
    monkeypatch.setattr(schedules_router, "start_scheduler_service", _fake_get_service)

    app = FastAPI()
    app.include_router(schedules_router.router)
    with TestClient(app) as client:
        create_resp = client.post(
            "/api/schedules",
            json={
                "owner_key": "web:settings",
                "schedule": {
                    "title": "Daily papers",
                    "prompt": "research xxx papers",
                    "kind": "cron",
                    "cron": "0 9 * * *",
                    "timezone": "Asia/Shanghai",
                },
                "confirmed": True,
            },
        )
        assert create_resp.status_code == 200
        schedule_id = create_resp.json()["data"]["schedule"]["id"]

        update_resp = client.patch(
            f"/api/schedules/{schedule_id}",
            json={"owner_key": "web:settings", "title": "Daily papers v2"},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["data"]["draft_id"] == update_resp.json()["data"]["draft"]["id"]

        trigger_resp = client.post(
            f"/api/schedules/{schedule_id}/trigger",
            json={"owner_key": "web:settings"},
        )
        assert trigger_resp.status_code == 200
        assert trigger_resp.json()["data"]["draft_id"] == trigger_resp.json()["data"]["draft"]["id"]

        delete_resp = client.delete(f"/api/schedules/{schedule_id}?owner_key=web:settings")
        assert delete_resp.status_code == 200
        assert delete_resp.json()["data"]["draft_id"] == delete_resp.json()["data"]["draft"]["id"]


def test_schedules_router_draft_confirm_requires_matching_owner_key(tmp_path: Path, monkeypatch) -> None:
    store = _make_store(tmp_path)
    fake_service = _FakeSchedulerService()

    async def _fake_get_service():
        return fake_service

    monkeypatch.setattr(schedules_router, "_store", lambda: store)
    monkeypatch.setattr(schedules_router, "get_scheduler_service", _fake_get_service)
    monkeypatch.setattr(schedules_router, "start_scheduler_service", _fake_get_service)

    app = FastAPI()
    app.include_router(schedules_router.router)
    with TestClient(app) as client:
        draft_resp = client.post(
            "/api/schedules",
            json={
                "owner_key": "telegram:user-123",
                "schedule": {
                    "title": "Telegram report",
                    "prompt": "prepare report",
                    "kind": "once",
                    "at": "2099-01-01T09:00:00+08:00",
                    "timezone": "Asia/Shanghai",
                },
                "confirmed": False,
            },
        )
        assert draft_resp.status_code == 200
        draft_id = draft_resp.json()["data"]["draft"]["id"]

        wrong_owner_confirm = client.post(
            f"/api/schedules/drafts/{draft_id}/confirm",
            json={"owner_key": "telegram:other-user"},
        )
        assert wrong_owner_confirm.status_code == 404

        confirm_resp = client.post(
            f"/api/schedules/drafts/{draft_id}/confirm",
            json={"owner_key": "telegram:user-123"},
        )
        assert confirm_resp.status_code == 200
        assert confirm_resp.json()["data"]["action"] == "add"
        confirmed = confirm_resp.json()["data"]["schedule"]
        assert confirmed["owner_key"] == "telegram:user-123"
        assert confirmed["owner_channel"] == "telegram"
        assert confirmed["owner_user"] == "user-123"


def test_router_confirm_failure_keeps_draft(tmp_path: Path, monkeypatch) -> None:
    store = _make_store(tmp_path)
    fake_service = _FakeSchedulerService()

    async def _fake_get_service():
        return fake_service

    monkeypatch.setattr(schedules_router, "_store", lambda: store)
    monkeypatch.setattr(schedules_router, "get_scheduler_service", _fake_get_service)
    monkeypatch.setattr(schedules_router, "start_scheduler_service", _fake_get_service)

    draft = store.create_draft(
        owner_key="web:settings",
        action="add",
        payload={
            "schedule": {
                "title": "Broken draft",
                "prompt": "prepare report",
                "kind": "not-a-kind",
                "at": "2099-01-01T09:00:00",
                "timezone": "Asia/Shanghai",
            },
            "meta": {"assistant_id": "lead_agent"},
        },
    )

    app = FastAPI()
    app.include_router(schedules_router.router)
    with TestClient(app) as client:
        confirm_resp = client.post(
            f"/api/schedules/drafts/{draft['id']}/confirm",
            json={"owner_key": "web:settings"},
        )
        assert confirm_resp.status_code == 422
        assert store.get_draft(owner_key="web:settings", draft_id=draft["id"]) is not None


def test_resume_expired_once_returns_422(tmp_path: Path, monkeypatch) -> None:
    store = _make_store(tmp_path)
    fake_service = _FakeSchedulerService()

    async def _fake_get_service():
        return fake_service

    monkeypatch.setattr(schedules_router, "_store", lambda: store)
    monkeypatch.setattr(schedules_router, "get_scheduler_service", _fake_get_service)
    monkeypatch.setattr(schedules_router, "start_scheduler_service", _fake_get_service)

    app = FastAPI()
    app.include_router(schedules_router.router)
    with TestClient(app) as client:
        create_resp = client.post(
            "/api/schedules",
            json={
                "owner_key": "web:settings",
                "schedule": {
                    "title": "Once",
                    "prompt": "run once",
                    "kind": "once",
                    "at": "2099-01-01T09:00:00+08:00",
                    "timezone": "Asia/Shanghai",
                },
                "confirmed": True,
            },
        )
        assert create_resp.status_code == 200
        schedule_id = create_resp.json()["data"]["schedule"]["id"]
        owner_key = create_resp.json()["data"]["schedule"]["owner_key"]

        with store._connect() as conn:  # noqa: SLF001 - intentional white-box test
            conn.execute(
                """
                UPDATE schedules
                SET status = 'paused', run_at_utc = ?, next_run_at = NULL
                WHERE id = ?
                """,
                ("2000-01-01T00:00:00+00:00", schedule_id),
            )

        resume_resp = client.post(f"/api/schedules/{schedule_id}/resume", json={"owner_key": owner_key})
        assert resume_resp.status_code == 422


@pytest.mark.anyio
async def test_gateway_lifespan_stops_scheduler_before_channel(monkeypatch) -> None:
    calls: list[str] = []

    class _FakeChannelService:
        def get_status(self) -> dict[str, object]:
            return {"service_running": True, "channels": {}}

    monkeypatch.setattr(gateway_app, "get_app_config", lambda: object())
    monkeypatch.setattr(
        gateway_app,
        "get_gateway_config",
        lambda: SimpleNamespace(host="127.0.0.1", port=8000),
    )

    async def _start_channel_service() -> _FakeChannelService:
        return _FakeChannelService()

    async def _start_scheduler_service() -> _FakeSchedulerService:
        return _FakeSchedulerService()

    async def _stop_scheduler_service() -> None:
        calls.append("scheduler")

    async def _stop_channel_service() -> None:
        calls.append("channel")

    monkeypatch.setattr("src.channels.service.start_channel_service", _start_channel_service)
    monkeypatch.setattr("src.scheduler.service.start_scheduler_service", _start_scheduler_service)
    monkeypatch.setattr("src.scheduler.service.stop_scheduler_service", _stop_scheduler_service)
    monkeypatch.setattr("src.channels.service.stop_channel_service", _stop_channel_service)

    async with gateway_app.lifespan(FastAPI()):
        pass

    assert calls == ["scheduler", "channel"]
