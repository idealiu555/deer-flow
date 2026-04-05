"""Shared draft-confirm execution for scheduler mutations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.scheduler.store import SchedulerStore, derive_owner_identity


class SchedulerDraftActionError(ValueError):
    """Raised when a scheduler draft cannot be confirmed."""

    def __init__(self, message: str, *, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _pick_meta(meta: Mapping[str, Any], fallback: Mapping[str, Any], key: str) -> Any:
    if key in meta:
        return meta.get(key)
    return fallback.get(key)


def _pick_dict_meta(meta: Mapping[str, Any], fallback: Mapping[str, Any], key: str) -> dict[str, Any]:
    raw = _pick_meta(meta, fallback, key)
    if isinstance(raw, Mapping):
        return dict(raw)
    return {}


def _normalize_schedule_kind(value: Any) -> str:
    kind = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if kind in {"once", "one_time", "oneoff", "one_off", "single"}:
        return "once"
    if kind in {"cron", "recurring", "repeat", "repeating"}:
        return "cron"
    return kind


def normalize_add_schedule_payload(store: SchedulerStore, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and canonicalize add-schedule payloads before persisting/executing."""
    draft_schedule = dict(payload)
    draft_schedule["kind"] = _normalize_schedule_kind(draft_schedule.get("kind"))
    normalized = store.normalize_schedule_payload(draft_schedule)

    canonical: dict[str, Any] = {
        "title": normalized["title"],
        "prompt": normalized["prompt"],
        "kind": normalized["kind"],
        "timezone": normalized["timezone"],
    }
    if normalized["kind"] == "cron":
        canonical["cron"] = normalized["cron_expr"]
    else:
        canonical["at"] = str(draft_schedule.get("at") or "").strip() or str(normalized["run_at_utc"] or "")
    return canonical


def normalize_schedule_patch_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize mutable schedule patch fields before persistence/execution."""
    normalized = dict(payload)
    if "kind" in normalized:
        normalized["kind"] = _normalize_schedule_kind(normalized.get("kind"))
    return normalized


def execute_confirmed_draft(
    *,
    store: SchedulerStore,
    draft: Mapping[str, Any],
    fallback_meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a consumed draft and return normalized mutation result."""
    action = str(draft.get("action") or "")
    owner_key = str(draft.get("owner_key") or "").strip()
    if not owner_key:
        raise SchedulerDraftActionError("Invalid draft payload")

    fallback = dict(fallback_meta or {})
    payload = draft.get("payload") if isinstance(draft.get("payload"), Mapping) else {}
    schedule_id = str(draft.get("schedule_id") or "").strip() or None

    if action == "add":
        schedule_payload = payload.get("schedule")
        meta = payload.get("meta")
        if not isinstance(schedule_payload, Mapping) or not isinstance(meta, Mapping):
            raise SchedulerDraftActionError("Invalid add draft payload")
        owner_channel, owner_user = derive_owner_identity(owner_key)
        created = store.create_schedule(
            owner_key=owner_key,
            owner_channel=owner_channel,
            owner_user=owner_user,
            channel_name=_pick_meta(meta, fallback, "channel_name"),
            chat_id=_pick_meta(meta, fallback, "chat_id"),
            topic_id=_pick_meta(meta, fallback, "topic_id"),
            thread_id=_pick_meta(meta, fallback, "thread_id"),
            assistant_id=str(_pick_meta(meta, fallback, "assistant_id") or "lead_agent"),
            payload=normalize_add_schedule_payload(store, schedule_payload),
            config=_pick_dict_meta(meta, fallback, "config"),
            context=_pick_dict_meta(meta, fallback, "context"),
        )
        return {"action": action, "schedule": created, "wake": True}

    if action == "update":
        if not schedule_id:
            raise SchedulerDraftActionError("Invalid update draft payload")
        patch_payload = normalize_schedule_patch_payload({key: value for key, value in dict(payload).items() if key != "_meta"})
        updated = store.update_schedule(schedule_id=schedule_id, owner_key=owner_key, patch=patch_payload)
        if updated is None:
            raise SchedulerDraftActionError("Schedule not found", status_code=404)
        return {"action": action, "schedule": updated, "wake": True}

    if action == "remove":
        if not schedule_id:
            raise SchedulerDraftActionError("Invalid remove draft payload")
        deleted = store.delete_schedule(schedule_id=schedule_id, owner_key=owner_key)
        if not deleted:
            raise SchedulerDraftActionError("Schedule not found", status_code=404)
        return {"action": action, "schedule_id": schedule_id, "wake": False}

    if action == "run":
        if not schedule_id:
            raise SchedulerDraftActionError("Invalid run draft payload")
        schedule = store.trigger_schedule(schedule_id=schedule_id, owner_key=owner_key)
        if schedule is None:
            raise SchedulerDraftActionError("Schedule not found", status_code=404)
        return {"action": action, "schedule": schedule, "wake": True}

    raise SchedulerDraftActionError(f"Unsupported draft action: {action}")
