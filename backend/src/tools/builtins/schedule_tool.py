"""Built-in structured scheduler tool."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from langchain.tools import ToolRuntime, tool

from src.agents.thread_state import ThreadRuntimeContext, ThreadState
from src.config.scheduler_config import SchedulerConfig, get_scheduler_config
from src.scheduler import (
    SchedulerValidationError,
    get_scheduler_store,
    resolve_owner_from_context,
    start_scheduler_service,
    wake_running_scheduler_service_best_effort,
)
from src.scheduler.draft_actions import (
    SchedulerDraftActionError,
    execute_confirmed_draft,
    normalize_add_schedule_payload,
    normalize_schedule_patch_payload,
)

Action = Literal["status", "list", "add", "update", "remove", "run", "runs", "wake", "confirm"]
logger = logging.getLogger(__name__)


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


_DROP = object()
_BLOCKED_RUNTIME_KEYS = {
    "checkpoint_id",
    "checkpoint_map",
    "checkpoint_ns",
    "created_by",
    "graph_id",
    "run_id",
}


def _is_blocked_runtime_key(key: str) -> bool:
    return key in _BLOCKED_RUNTIME_KEYS or key.startswith("langgraph_") or key.startswith("__")


def _to_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            safe = _to_json_safe(item)
            if safe is _DROP:
                continue
            out[str(key)] = safe
        return out
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        out: list[Any] = []
        for item in value:
            safe = _to_json_safe(item)
            if safe is _DROP:
                continue
            out.append(safe)
        return out
    return _DROP


def _snapshot_runtime(run_config: Mapping[str, Any], run_context: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    # Persist only stable config knobs; run-specific transport metadata breaks history persistence.
    safe_config: dict[str, Any] = {}
    recursion_limit = run_config.get("recursion_limit")
    if isinstance(recursion_limit, int) and recursion_limit > 0:
        safe_config["recursion_limit"] = recursion_limit

    merged_context = dict(run_context or {})
    configurable = run_config.get("configurable")
    if isinstance(configurable, Mapping):
        for key, value in configurable.items():
            normalized_key = str(key)
            if _is_blocked_runtime_key(normalized_key):
                continue
            safe_value = _to_json_safe(value)
            if safe_value is _DROP:
                continue
            merged_context.setdefault(normalized_key, safe_value)

    safe_context = _to_json_safe(merged_context)
    if not isinstance(safe_context, dict):
        return safe_config, {}

    pruned_context: dict[str, Any] = {}
    for key, value in safe_context.items():
        normalized_key = str(key)
        if _is_blocked_runtime_key(normalized_key):
            continue
        pruned_context[normalized_key] = value
    return safe_config, pruned_context


def _parse_query_int(value: Any, *, field: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise SchedulerValidationError(f"query.{field} must be an integer") from exc


def _latest_human_message_meta(state: Mapping[str, Any] | None) -> tuple[str, int]:
    if not isinstance(state, Mapping):
        return "", 0
    messages = state.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, str | bytes | bytearray):
        return "", 0
    latest_message_id = ""
    human_message_count = 0
    for message in messages:
        if isinstance(message, Mapping):
            role = str(message.get("type") or message.get("role") or "").strip().lower()
            name = str(message.get("name") or "").strip()
            message_id = str(message.get("id") or "").strip()
        else:
            role = str(getattr(message, "type", "") or getattr(message, "role", "")).strip().lower()
            name = str(getattr(message, "name", "") or "").strip()
            message_id = str(getattr(message, "id", "") or "").strip()
        if role not in {"human", "user"}:
            continue
        if name == "todo_reminder":
            continue
        human_message_count += 1
        latest_message_id = message_id
    return latest_message_id, human_message_count


def _origin_user_meta(state: Mapping[str, Any] | None) -> dict[str, Any]:
    message_id, message_count = _latest_human_message_meta(state)
    meta: dict[str, Any] = {}
    if message_id:
        meta["origin_user_message_id"] = message_id
    if message_count > 0:
        meta["origin_user_message_count"] = message_count
    return meta


def _draft_meta(draft: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = draft.get("payload")
    if isinstance(payload, Mapping):
        add_meta = payload.get("meta")
        mutation_meta = payload.get("_meta")
        if isinstance(add_meta, Mapping):
            return add_meta
        if isinstance(mutation_meta, Mapping):
            return mutation_meta
    return {}


def _draft_thread_id(draft: Mapping[str, Any]) -> str:
    return str(_draft_meta(draft).get("thread_id") or "").strip()


def _is_follow_up_user_confirmation(state: Mapping[str, Any] | None, draft: Mapping[str, Any]) -> bool:
    meta = _draft_meta(draft)
    origin_message_id = str(meta.get("origin_user_message_id") or "").strip()

    origin_message_count = 0
    raw_origin_count = meta.get("origin_user_message_count")
    if isinstance(raw_origin_count, int):
        origin_message_count = raw_origin_count

    if not origin_message_id and origin_message_count <= 0:
        # Backward compatibility for old drafts or runtime without state metadata.
        return True

    if not isinstance(state, Mapping):
        return False

    latest_message_id, human_message_count = _latest_human_message_meta(state)
    if human_message_count <= 0:
        return False
    if origin_message_count > 0 and human_message_count <= origin_message_count:
        return False
    if origin_message_id and latest_message_id and origin_message_id == latest_message_id:
        return False
    return True


def _resolve_confirm_draft_id(store: Any, *, owner_key: str, runtime_context: Mapping[str, Any], state: Mapping[str, Any] | None) -> tuple[str | None, str | None]:
    list_drafts = getattr(store, "list_drafts", None)
    if not callable(list_drafts):
        return None, "draft_id is required"

    runtime_thread_id = str(runtime_context.get("thread_id") or "").strip()
    eligible: list[Mapping[str, Any]] = []
    for draft in list_drafts(owner_key=owner_key, limit=20):
        if runtime_thread_id and _draft_thread_id(draft) not in {"", runtime_thread_id}:
            continue
        if not _is_follow_up_user_confirmation(state, draft):
            continue
        eligible.append(draft)

    if len(eligible) == 1:
        return str(eligible[0].get("id") or "").strip() or None, None
    if len(eligible) > 1:
        return None, "Multiple pending drafts match this confirmation; draft_id is required"
    return None, "draft_id is required"


def _resolve_owner_key(owner: Mapping[str, Any], query: Mapping[str, Any] | None) -> str:
    override = str((query or {}).get("owner_key") or "").strip()
    if override:
        return override
    return str(owner.get("owner_key") or "").strip() or "web:settings"


def _draft_response(*, action: Action, draft: Mapping[str, Any], message: str) -> str:
    return _json(
        {
            "success": True,
            "action": action,
            "confirmed": False,
            "requires_confirmation": True,
            "message": message,
            "draft_id": draft["id"],
            "draft": dict(draft),
        }
    )


def _confirm_response(*, action: Action, result: Mapping[str, Any]) -> str:
    response: dict[str, Any] = {
        "success": True,
        "action": action,
        "confirmed": True,
        "confirmed_action": result["action"],
    }
    if "schedule" in result:
        response["schedule"] = result["schedule"]
    if "schedule_id" in result:
        response["schedule_id"] = result["schedule_id"]
    return _json(response)


def _schedule_exists(store: Any, *, schedule_id: str, owner_key: str) -> bool:
    get_schedule = getattr(store, "get_schedule", None)
    if not callable(get_schedule):
        return True
    return get_schedule(schedule_id=schedule_id, owner_key=owner_key) is not None


async def _wake_scheduler_loop() -> bool:
    if wake_running_scheduler_service_best_effort():
        return True
    try:
        service = await start_scheduler_service()
    except Exception:
        logger.exception("Failed to start scheduler service from schedule tool wake path")
        return False
    if not bool(getattr(service, "_running", False)):
        return False
    service.wake()
    return True


@tool("schedule", parse_docstring=True)
async def schedule_tool(
    runtime: ToolRuntime[ThreadRuntimeContext, ThreadState],
    action: Action,
    schedule_id: str | None = None,
    draft_id: str | None = None,
    confirmed: bool = False,
    schedule: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
) -> str:
    """Manage recurring/one-time schedules via structured actions.

    This tool is the only supported way to manage scheduled tasks. Always prefer
    structured arguments over text parsing.

    Actions:
    - status: Get scheduler status metrics.
    - list: List schedules in current owner scope.
    - add: Create a draft for a schedule (confirmation required via action=confirm).
    - update: Create a draft to update schedule fields (must be executed by action=confirm).
    - remove: Create a draft to delete a schedule (must be executed by action=confirm).
    - run: Create a draft to queue immediate execution (must be executed by action=confirm).
    - runs: List execution runs of a schedule.
    - wake: Wake scheduler loop immediately.
    - confirm: Confirm and execute a previously created draft.

    Args:
        action: Scheduler action name.
        schedule_id: Schedule ID for update/remove/run/runs.
        draft_id: Draft ID for confirm action.
        confirmed: Deprecated for mutating actions in tool mode; add/update/remove/run always create drafts.
        schedule: Schedule payload. For add: {title,prompt,kind,cron|at,timezone}. For update: patch fields.
        query: Optional filters (e.g., {status,limit,offset,owner_key}).
    """
    store = get_scheduler_store(get_scheduler_config() or SchedulerConfig())
    owner = resolve_owner_from_context(runtime.context)
    owner_key = _resolve_owner_key(owner, query)
    state = getattr(runtime, "state", None)
    _ = confirmed  # Backward compatibility for older tool invocations; mutating actions remain draft-first.

    # Capture a deterministic execution snapshot for consistency.
    metadata = dict((runtime.config or {}).get("metadata", {}))
    assistant_id = str(owner.get("assistant_id") or runtime.context.get("assistant_id") or metadata.get("assistant_id") or "lead_agent")
    run_config, run_context = _snapshot_runtime(dict(runtime.config or {}), dict(runtime.context or {}))

    try:
        if action == "status":
            status = store.get_status()
            return _json({"success": True, "action": action, "status": status})

        if action == "list":
            params = query or {}
            schedules = store.list_schedules(
                owner_key=owner_key,
                status=str(params.get("status") or "") or None,
                limit=_parse_query_int(params.get("limit"), field="limit", default=20),
                offset=_parse_query_int(params.get("offset"), field="offset", default=0),
            )
            return _json({"success": True, "action": action, "schedules": schedules, "count": len(schedules)})

        if action == "runs":
            if not schedule_id:
                return _json({"success": False, "action": action, "error": "schedule_id is required"})
            if not _schedule_exists(store, schedule_id=schedule_id, owner_key=owner_key):
                return _json({"success": False, "action": action, "error": "Schedule not found"})
            limit = _parse_query_int((query or {}).get("limit"), field="limit", default=20)
            runs = store.list_runs(schedule_id=schedule_id, owner_key=owner_key, limit=limit)
            return _json({"success": True, "action": action, "runs": runs, "count": len(runs)})

        if action == "wake":
            woke = await _wake_scheduler_loop()
            message = "Scheduler loop awakened" if woke else "No running scheduler service to wake"
            return _json({"success": True, "action": action, "message": message})

        if action == "confirm":
            if not draft_id:
                draft_id, resolution_error = _resolve_confirm_draft_id(
                    store,
                    owner_key=owner_key,
                    runtime_context=runtime.context,
                    state=state,
                )
                if not draft_id:
                    return _json({"success": False, "action": action, "error": resolution_error or "draft_id is required"})
            draft = store.get_draft(owner_key=owner_key, draft_id=draft_id)
            if draft is None:
                return _json({"success": False, "action": action, "error": "Draft not found or expired"})
            if not _is_follow_up_user_confirmation(state, draft):
                return _json(
                    {
                        "success": False,
                        "action": action,
                        "error": "A follow-up user message with confirmation intent is required before confirming this draft",
                    }
                )
            try:
                result = execute_confirmed_draft(
                    store=store,
                    draft=draft,
                    fallback_meta={
                        "channel_name": owner.get("channel_name"),
                        "chat_id": owner.get("chat_id"),
                        "topic_id": owner.get("topic_id"),
                        "thread_id": owner.get("thread_id"),
                        "assistant_id": assistant_id,
                        "config": run_config,
                        "context": run_context,
                    },
                )
            except (SchedulerDraftActionError, SchedulerValidationError) as exc:
                return _json({"success": False, "action": action, "draft_id": draft_id, "error": str(exc)})
            store.delete_draft(owner_key=owner_key, draft_id=draft_id)
            if bool(result.get("wake")):
                await _wake_scheduler_loop()
            return _confirm_response(action=action, result=result)

        if action == "add":
            prepared_schedule = normalize_add_schedule_payload(store, schedule or {})
            draft_meta: dict[str, Any] = {
                "channel_name": owner.get("channel_name"),
                "chat_id": owner.get("chat_id"),
                "topic_id": owner.get("topic_id"),
                "thread_id": owner.get("thread_id"),
                "assistant_id": assistant_id,
                "config": run_config,
                "context": run_context,
            }
            draft_meta.update(_origin_user_meta(state))
            draft = store.create_draft(
                owner_key=owner_key,
                action="add",
                payload={
                    "schedule": prepared_schedule,
                    "meta": draft_meta,
                },
            )
            return _draft_response(
                action=action,
                draft=draft,
                message="Draft created. The schedule is not active yet. Confirm it with schedule(action='confirm', draft_id=...).",
            )

        if action in {"update", "remove", "run"} and not schedule_id:
            return _json({"success": False, "action": action, "error": "schedule_id is required"})

        # Mutating actions in tool mode are always draft-first for safety and consistency.
        if action in {"update", "remove", "run"}:
            if not _schedule_exists(store, schedule_id=schedule_id, owner_key=owner_key):
                return _json({"success": False, "action": action, "error": "Schedule not found"})

            draft_payload = normalize_schedule_patch_payload(schedule or {}) if action == "update" else dict(schedule or {})
            draft_meta = _origin_user_meta(state)
            if draft_meta:
                draft_payload["_meta"] = draft_meta
            draft = store.create_draft(owner_key=owner_key, action=action, schedule_id=schedule_id, payload=draft_payload)
            return _draft_response(
                action=action,
                draft=draft,
                message="Draft created. Please ask user to confirm, then call schedule(action='confirm', draft_id=...).",
            )

        return _json({"success": False, "action": action, "error": f"Unsupported action: {action}"})
    except SchedulerValidationError as exc:
        return _json({"success": False, "action": action, "error": str(exc)})
