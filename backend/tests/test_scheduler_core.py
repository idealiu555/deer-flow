"""Core scheduler store tests."""

from __future__ import annotations

from pathlib import Path

import pytest

import src.scheduler.store as scheduler_store_module
from src.config.app_config import AppConfig
from src.config.scheduler_config import SchedulerConfig, reset_scheduler_config
from src.config.scheduler_config import get_scheduler_config as get_scheduler_override
from src.scheduler.store import SchedulerStore, SchedulerValidationError, resolve_owner_from_context
from src.tools.tools import BUILTIN_TOOLS


def _make_store(tmp_path: Path) -> SchedulerStore:
    cfg = SchedulerConfig(
        db_path=str(tmp_path / "scheduler.db"),
        poll_interval_seconds=1,
        max_concurrency=1,
        lease_seconds=30,
        draft_ttl_seconds=3600,
        max_runs_per_schedule=10,
        retry_attempts=1,
        default_timezone="Asia/Shanghai",
    )
    return SchedulerStore(cfg)


def _write_config(path: Path, *, include_scheduler: bool) -> None:
    scheduler_block = ""
    if include_scheduler:
        scheduler_block = """
scheduler:
  enabled: true
  db_path: scheduler/override.db
"""

    path.write_text(
        (
            """
sandbox:
  use: src.sandbox.local:LocalSandboxProvider
models: []
tools: []
tool_groups: []
"""
            + scheduler_block
        ).strip()
        + "\n",
        encoding="utf-8",
    )


def test_schedule_tool_is_registered() -> None:
    names = {tool.name for tool in BUILTIN_TOOLS}
    assert "schedule" in names


def test_create_list_trigger_claim_release(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

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
        config={"recursion_limit": 100},
        context={"thread_id": "t1"},
    )

    assert created["id"]
    assert created["status"] == "active"
    assert created["next_run_at"] is not None

    listed = store.list_schedules(owner_key="web:test")
    assert len(listed) == 1
    assert listed[0]["id"] == created["id"]

    queued = store.trigger_schedule(schedule_id=created["id"], owner_key="web:test")
    assert queued is not None

    claimed = store.claim_due_schedules(limit=1, lease_owner="tester", lease_seconds=30)
    assert len(claimed) == 1
    assert claimed[0]["id"] == created["id"]

    released = store.release_schedule_claim(schedule_id=created["id"], lease_owner="tester", success=True)
    assert released is not None
    assert released["status"] == "active"


def test_claim_due_schedules_can_exclude_channel_targets(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    regular = store.create_schedule(
        owner_key="web:test",
        owner_channel="web",
        owner_user="u1",
        channel_name=None,
        chat_id=None,
        topic_id=None,
        thread_id="t1",
        assistant_id="lead_agent",
        payload={
            "title": "Regular",
            "prompt": "Run local task",
            "kind": "cron",
            "cron": "*/5 * * * *",
            "timezone": "Asia/Shanghai",
        },
        config={},
        context={},
    )
    channel = store.create_schedule(
        owner_key="telegram:u1",
        owner_channel="telegram",
        owner_user="u1",
        channel_name="telegram",
        chat_id="chat-1",
        topic_id=None,
        thread_id="t2",
        assistant_id="lead_agent",
        payload={
            "title": "Channel",
            "prompt": "Send message",
            "kind": "cron",
            "cron": "*/5 * * * *",
            "timezone": "Asia/Shanghai",
        },
        config={},
        context={},
    )

    store.trigger_schedule(schedule_id=regular["id"], owner_key="web:test")
    store.trigger_schedule(schedule_id=channel["id"], owner_key="telegram:u1")

    claimed = store.claim_due_schedules(
        limit=10,
        lease_owner="worker-local",
        lease_seconds=30,
        include_channel_targets=False,
    )
    claimed_ids = {item["id"] for item in claimed}
    assert regular["id"] in claimed_ids
    assert channel["id"] not in claimed_ids


def test_draft_lifecycle(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    draft = store.create_draft(
        owner_key="web:test",
        action="add",
        payload={"schedule": {"title": "x"}},
    )
    assert draft["id"]

    consumed = store.consume_draft(owner_key="web:test", draft_id=draft["id"])
    assert consumed is not None
    assert consumed["id"] == draft["id"]

    missing = store.consume_draft(owner_key="web:test", draft_id=draft["id"])
    assert missing is None


def test_resume_once_without_future_run_is_rejected(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

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
            "title": "One-time report",
            "prompt": "prepare report",
            "kind": "once",
            "at": "2099-01-01T09:00:00+08:00",
            "timezone": "Asia/Shanghai",
        },
        config={},
        context={},
    )

    # Simulate an already-expired once schedule persisted in paused state.
    with store._connect() as conn:  # noqa: SLF001 - intentional white-box test
        conn.execute(
            """
            UPDATE schedules
            SET status = 'paused', run_at_utc = ?, next_run_at = NULL
            WHERE id = ?
            """,
            ("2000-01-01T00:00:00+00:00", created["id"]),
        )

    with pytest.raises(SchedulerValidationError, match="cannot be activated"):
        store.set_schedule_status(schedule_id=created["id"], owner_key="web:test", status="active")


def test_create_once_with_invalid_at_raises_validation_error(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    with pytest.raises(SchedulerValidationError, match="Invalid schedule.at"):
        store.create_schedule(
            owner_key="web:test",
            owner_channel="web",
            owner_user="u1",
            channel_name=None,
            chat_id=None,
            topic_id=None,
            thread_id="t1",
            assistant_id="lead_agent",
            payload={
                "title": "One-time report",
                "prompt": "prepare report",
                "kind": "once",
                "at": "not-a-timestamp",
                "timezone": "Asia/Shanghai",
            },
            config={},
            context={},
        )


def test_trigger_while_leased_queues_single_followup_run(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

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

    # Make it due and claim it (simulates currently-running execution).
    store.trigger_schedule(schedule_id=schedule_id, owner_key="web:test")
    claimed = store.claim_due_schedules(limit=1, lease_owner="worker-a", lease_seconds=60)
    assert [item["id"] for item in claimed] == [schedule_id]

    # Run-now during active lease should queue exactly one follow-up run.
    queued = store.trigger_schedule(schedule_id=schedule_id, owner_key="web:test")
    assert queued is not None
    assert queued["run_now_pending"] is True

    with store._connect() as conn:  # noqa: SLF001 - intentional white-box test
        row = conn.execute("SELECT lease_owner, run_now_pending FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
    assert row is not None
    assert row["lease_owner"] == "worker-a"
    assert row["run_now_pending"] == 1

    # Releasing the current run consumes pending flag and schedules one immediate follow-up.
    released = store.release_schedule_claim(schedule_id=schedule_id, lease_owner="worker-a", success=True)
    assert released is not None
    assert released["status"] == "active"
    assert released["run_now_pending"] is False
    assert released["next_run_at"] is not None

    followup = store.claim_due_schedules(limit=1, lease_owner="worker-b", lease_seconds=60)
    assert [item["id"] for item in followup] == [schedule_id]


def test_update_paused_while_leased_clears_pending_and_stays_paused(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

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
    claimed = store.claim_due_schedules(limit=1, lease_owner="worker-a", lease_seconds=60)
    assert [item["id"] for item in claimed] == [schedule_id]

    store.trigger_schedule(schedule_id=schedule_id, owner_key="web:test")
    updated = store.update_schedule(schedule_id=schedule_id, owner_key="web:test", patch={"status": "paused"})
    assert updated is not None
    assert updated["status"] == "paused"
    assert updated["next_run_at"] is None
    assert updated["run_now_pending"] is False

    released = store.release_schedule_claim(schedule_id=schedule_id, lease_owner="worker-a", success=True)
    assert released is not None
    assert released["status"] == "paused"
    assert released["next_run_at"] is None
    assert released["run_now_pending"] is False

    followup = store.claim_due_schedules(limit=1, lease_owner="worker-b", lease_seconds=60)
    assert followup == []


def test_set_schedule_status_paused_preserves_active_lease_until_release(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

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
    claimed = store.claim_due_schedules(limit=1, lease_owner="worker-a", lease_seconds=60)
    assert [item["id"] for item in claimed] == [schedule_id]

    paused = store.set_schedule_status(schedule_id=schedule_id, owner_key="web:test", status="paused")
    assert paused is not None
    assert paused["status"] == "paused"
    assert paused["next_run_at"] is None
    assert paused["run_now_pending"] is False

    with store._connect() as conn:  # noqa: SLF001 - intentional white-box test
        row = conn.execute("SELECT lease_owner FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
    assert row is not None
    assert row["lease_owner"] == "worker-a"

    released = store.release_schedule_claim(schedule_id=schedule_id, lease_owner="worker-a", success=False, error="boom")
    assert released is not None
    assert released["status"] == "paused"
    assert released["next_run_at"] is None
    assert released["run_now_pending"] is False
    assert released["last_error"] == "boom"

def test_renew_schedule_lease_extends_expiration_for_same_owner(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
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
    claimed = store.claim_due_schedules(limit=1, lease_owner="worker-a", lease_seconds=30)
    assert [item["id"] for item in claimed] == [schedule_id]

    with store._connect() as conn:  # noqa: SLF001 - intentional white-box test
        before = conn.execute("SELECT lease_expires_at FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
    assert before is not None
    before_exp = before["lease_expires_at"]

    assert store.renew_schedule_lease(schedule_id=schedule_id, lease_owner="worker-a", lease_seconds=120) is True
    assert store.renew_schedule_lease(schedule_id=schedule_id, lease_owner="worker-b", lease_seconds=120) is False

    with store._connect() as conn:  # noqa: SLF001 - intentional white-box test
        after = conn.execute("SELECT lease_expires_at FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
    assert after is not None
    assert after["lease_expires_at"] > before_exp


def test_release_schedule_claim_requires_matching_lease_owner(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
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
    claimed = store.claim_due_schedules(limit=1, lease_owner="worker-a", lease_seconds=60)
    assert [item["id"] for item in claimed] == [schedule_id]

    with store._connect() as conn:  # noqa: SLF001 - intentional white-box test
        conn.execute("UPDATE schedules SET lease_owner = ? WHERE id = ?", ("worker-b", schedule_id))

    released = store.release_schedule_claim(schedule_id=schedule_id, lease_owner="worker-a", success=True)
    assert released is None

    with store._connect() as conn:  # noqa: SLF001 - intentional white-box test
        row = conn.execute("SELECT lease_owner FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
    assert row is not None
    assert row["lease_owner"] == "worker-b"


def test_update_title_prompt_does_not_shift_next_run_at(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

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
            "cron": "0 9 * * *",
            "timezone": "Asia/Shanghai",
        },
        config={},
        context={},
    )
    next_run_at_before = created["next_run_at"]
    assert next_run_at_before is not None

    updated = store.update_schedule(
        schedule_id=created["id"],
        owner_key="web:test",
        patch={
            "title": "Daily research v2",
            "prompt": "Updated prompt only",
        },
    )
    assert updated is not None
    assert updated["title"] == "Daily research v2"
    assert updated["prompt"] == "Updated prompt only"
    assert updated["next_run_at"] == next_run_at_before


def test_update_with_null_text_fields_keeps_original_values(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

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
            "title": "Original title",
            "prompt": "Original prompt",
            "kind": "cron",
            "cron": "0 9 * * *",
            "timezone": "Asia/Shanghai",
        },
        config={},
        context={},
    )

    updated = store.update_schedule(
        schedule_id=created["id"],
        owner_key="web:test",
        patch={"title": None, "prompt": None},
    )
    assert updated is not None
    assert updated["title"] == "Original title"
    assert updated["prompt"] == "Original prompt"


def test_update_rejects_empty_title_or_prompt_even_without_timing_change(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

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
            "title": "Original title",
            "prompt": "Original prompt",
            "kind": "cron",
            "cron": "0 9 * * *",
            "timezone": "Asia/Shanghai",
        },
        config={},
        context={},
    )

    with pytest.raises(SchedulerValidationError, match="schedule.title is required"):
        store.update_schedule(schedule_id=created["id"], owner_key="web:test", patch={"title": ""})

    with pytest.raises(SchedulerValidationError, match="schedule.prompt is required"):
        store.update_schedule(schedule_id=created["id"], owner_key="web:test", patch={"prompt": "   "})


def test_update_preserves_active_lease_and_blocks_reclaim(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

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
    claimed = store.claim_due_schedules(limit=1, lease_owner="worker-a", lease_seconds=60)
    assert [item["id"] for item in claimed] == [schedule_id]

    updated = store.update_schedule(
        schedule_id=schedule_id,
        owner_key="web:test",
        patch={"title": "Daily research v2"},
    )
    assert updated is not None
    assert updated["title"] == "Daily research v2"

    with store._connect() as conn:  # noqa: SLF001 - intentional white-box test
        row = conn.execute("SELECT lease_owner, lease_expires_at FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
    assert row is not None
    assert row["lease_owner"] == "worker-a"
    assert row["lease_expires_at"] is not None

    reclaimed = store.claim_due_schedules(limit=1, lease_owner="worker-b", lease_seconds=60)
    assert reclaimed == []


def test_list_schedules_rejects_invalid_filters(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    with pytest.raises(SchedulerValidationError, match="status filter"):
        store.list_schedules(owner_key="web:test", status="running")
    with pytest.raises(SchedulerValidationError, match="status filter"):
        store.list_schedules(owner_key="web:test", status="ACTIVE")

    with pytest.raises(SchedulerValidationError, match="owner_key must be non-empty"):
        store.list_schedules(owner_key="   ")


def test_list_runs_rejects_empty_owner_filter(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    with pytest.raises(SchedulerValidationError, match="owner_key must be non-empty"):
        store.list_runs(schedule_id="sched-1", owner_key="  ")


def test_resolve_owner_context_defaults_to_web_settings() -> None:
    resolved = resolve_owner_from_context({"thread_id": "thread-a"})
    assert resolved["owner_key"] == "web:settings"
    assert resolved["owner_channel"] == "web"
    assert resolved["owner_user"] == "settings"
    assert resolved["thread_id"] == "thread-a"


def test_resolve_owner_context_honors_explicit_owner_key() -> None:
    resolved = resolve_owner_from_context({"thread_id": "thread-a", "owner_key": "web:alice"})
    assert resolved["owner_key"] == "web:alice"
    assert resolved["owner_channel"] == "web"
    assert resolved["owner_user"] == "alice"


def test_get_scheduler_store_reinitializes_when_config_changes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(scheduler_store_module, "_store", None)
    monkeypatch.setattr(scheduler_store_module, "_store_key", None)

    cfg_a = SchedulerConfig(db_path=str(tmp_path / "scheduler-a.db"))
    cfg_b = SchedulerConfig(db_path=str(tmp_path / "scheduler-b.db"))

    store_a = scheduler_store_module.get_scheduler_store(cfg_a)
    same_as_a = scheduler_store_module.get_scheduler_store(cfg_a)
    store_b = scheduler_store_module.get_scheduler_store(cfg_b)

    assert same_as_a is store_a
    assert store_b is not store_a
    assert str(store_b._db_path).endswith("scheduler-b.db")  # noqa: SLF001 - singleton cache behavior check


def test_app_config_resets_scheduler_override_when_scheduler_block_removed(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    reset_scheduler_config()
    try:
        _write_config(config_path, include_scheduler=True)
        AppConfig.from_file(str(config_path))
        loaded = get_scheduler_override()
        assert loaded is not None
        assert loaded.db_path == "scheduler/override.db"

        _write_config(config_path, include_scheduler=False)
        AppConfig.from_file(str(config_path))
        assert get_scheduler_override() is None
    finally:
        reset_scheduler_config()
