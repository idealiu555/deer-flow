"""Gateway router for scheduler management."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, StringConstraints

from src.config.scheduler_config import SchedulerConfig, get_scheduler_config
from src.scheduler import (
    SchedulerValidationError,
    get_scheduler_service,
    get_scheduler_store,
    start_scheduler_service,
    wake_running_scheduler_service_best_effort,
)
from src.scheduler.draft_actions import SchedulerDraftActionError, execute_confirmed_draft, normalize_add_schedule_payload

router = APIRouter(prefix="/api/schedules", tags=["schedules"])
logger = logging.getLogger(__name__)

OwnerKey = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class SchedulePayload(BaseModel):
    title: str
    prompt: str
    kind: Literal["cron", "once"]
    cron: str | None = None
    at: str | None = None
    timezone: str | None = None


class ScheduleCreateRequest(BaseModel):
    schedule: SchedulePayload
    owner_key: OwnerKey
    channel_name: str | None = None
    chat_id: str | None = None
    topic_id: str | None = None
    thread_id: str | None = None
    assistant_id: str = "lead_agent"
    config: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    confirmed: bool = False


class SchedulePatchRequest(BaseModel):
    title: str | None = None
    prompt: str | None = None
    kind: Literal["cron", "once"] | None = None
    cron: str | None = None
    at: str | None = None
    timezone: str | None = None
    status: Literal["active", "paused"] | None = None
    owner_key: OwnerKey
    confirmed: bool = False


class ScheduleActionRequest(BaseModel):
    owner_key: OwnerKey
    confirmed: bool = False


class ScheduleOwnerRequest(BaseModel):
    owner_key: OwnerKey


class DraftConfirmRequest(BaseModel):
    owner_key: OwnerKey


class ApiResponse(BaseModel):
    success: bool
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


def _store():
    return get_scheduler_store(get_scheduler_config() or SchedulerConfig())


def _draft_response(draft: Mapping[str, Any]) -> ApiResponse:
    return ApiResponse(
        success=True,
        message="Draft created",
        data={"confirmed": False, "requires_confirmation": True, "draft_id": draft["id"], "draft": dict(draft)},
    )


def _result_data(result: Mapping[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {"action": result["action"]}
    if "schedule" in result:
        data["schedule"] = result["schedule"]
    if "schedule_id" in result:
        data["schedule_id"] = result["schedule_id"]
    return data


def _execute_schedule_action(
    *,
    store,
    draft: Mapping[str, Any],
    fallback_meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        return execute_confirmed_draft(store=store, draft=draft, fallback_meta=fallback_meta)
    except SchedulerValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SchedulerDraftActionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _build_add_draft(
    *,
    store,
    owner_key: str,
    payload: Mapping[str, Any],
    channel_name: str | None,
    chat_id: str | None,
    topic_id: str | None,
    thread_id: str | None,
    assistant_id: str,
    config: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    canonical_payload = normalize_add_schedule_payload(store, payload)
    return {
        "owner_key": owner_key,
        "action": "add",
        "payload": {
            "schedule": canonical_payload,
            "meta": {
                "channel_name": channel_name,
                "chat_id": chat_id,
                "topic_id": topic_id,
                "thread_id": thread_id,
                "assistant_id": assistant_id,
                "config": dict(config),
                "context": dict(context),
            },
        },
    }


def _build_existing_schedule_action(
    *,
    owner_key: str,
    schedule_id: str,
    action: Literal["update", "remove", "run"],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "owner_key": owner_key,
        "action": action,
        "schedule_id": schedule_id,
        "payload": dict(payload),
    }


def _create_draft_for_existing_schedule(
    *,
    store,
    owner_key: str,
    schedule_id: str,
    action: Literal["update", "remove", "run"],
    payload: dict[str, Any],
) -> dict[str, Any]:
    if store.get_schedule(schedule_id=schedule_id, owner_key=owner_key) is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return store.create_draft(owner_key=owner_key, action=action, schedule_id=schedule_id, payload=payload)


def _set_schedule_status(
    *,
    schedule_id: str,
    owner_key: str,
    status: Literal["active", "paused"],
) -> dict[str, Any]:
    try:
        schedule = _store().set_schedule_status(schedule_id=schedule_id, owner_key=owner_key, status=status)
    except SchedulerValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return schedule


async def _wake_scheduler() -> None:
    if wake_running_scheduler_service_best_effort():
        return
    try:
        service = await start_scheduler_service()
    except Exception:
        logger.exception("Failed to start scheduler service while waking from schedules router")
        return
    if bool(getattr(service, "_running", False)):
        service.wake()


@router.get("", response_model=ApiResponse)
async def list_schedules(
    owner_key: OwnerKey = Query(...),
    status: Literal["active", "paused"] | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse:
    schedules = _store().list_schedules(owner_key=owner_key, status=status, limit=limit, offset=offset)
    return ApiResponse(success=True, message="ok", data={"schedules": schedules, "count": len(schedules)})


@router.get("/status", response_model=ApiResponse)
async def get_scheduler_status() -> ApiResponse:
    status = _store().get_status()
    service = await get_scheduler_service()
    status["service_running"] = bool(getattr(service, "_running", False))
    return ApiResponse(success=True, message="ok", data={"scheduler_status": status})


@router.post("", response_model=ApiResponse)
async def create_schedule(request: ScheduleCreateRequest) -> ApiResponse:
    store = _store()
    owner_key = request.owner_key
    payload = request.schedule.model_dump(exclude_none=True)
    draft_payload = _build_add_draft(
        store=store,
        owner_key=owner_key,
        payload=payload,
        channel_name=request.channel_name,
        chat_id=request.chat_id,
        topic_id=request.topic_id,
        thread_id=request.thread_id,
        assistant_id=request.assistant_id,
        config=request.config,
        context=request.context,
    )

    if not request.confirmed:
        draft = store.create_draft(owner_key=owner_key, action="add", payload=draft_payload["payload"])
        return _draft_response(draft)

    result = _execute_schedule_action(store=store, draft=draft_payload)
    if bool(result.get("wake")):
        await _wake_scheduler()
    return ApiResponse(success=True, message="Schedule created", data={"confirmed": True, **_result_data(result)})


@router.patch("/{schedule_id}", response_model=ApiResponse)
async def update_schedule(schedule_id: str, request: SchedulePatchRequest) -> ApiResponse:
    store = _store()
    owner_key = request.owner_key
    patch = request.model_dump(exclude_none=True, exclude={"owner_key", "confirmed"})

    if not patch:
        raise HTTPException(status_code=400, detail="No patch fields provided")

    if not request.confirmed:
        draft = _create_draft_for_existing_schedule(
            store=store,
            owner_key=owner_key,
            schedule_id=schedule_id,
            action="update",
            payload=patch,
        )
        return _draft_response(draft)

    result = _execute_schedule_action(
        store=store,
        draft=_build_existing_schedule_action(owner_key=owner_key, schedule_id=schedule_id, action="update", payload=patch),
    )
    if bool(result.get("wake")):
        await _wake_scheduler()
    return ApiResponse(success=True, message="Schedule updated", data={"confirmed": True, **_result_data(result)})


@router.delete("/{schedule_id}", response_model=ApiResponse)
async def delete_schedule(schedule_id: str, owner_key: OwnerKey = Query(...), confirmed: bool = Query(default=False)) -> ApiResponse:
    store = _store()

    if not confirmed:
        draft = _create_draft_for_existing_schedule(
            store=store,
            owner_key=owner_key,
            schedule_id=schedule_id,
            action="remove",
            payload={},
        )
        return _draft_response(draft)

    result = _execute_schedule_action(
        store=store,
        draft=_build_existing_schedule_action(owner_key=owner_key, schedule_id=schedule_id, action="remove", payload={}),
    )
    return ApiResponse(success=True, message="Schedule deleted", data={"confirmed": True, **_result_data(result)})


@router.post("/{schedule_id}/trigger", response_model=ApiResponse)
async def trigger_schedule(schedule_id: str, request: ScheduleActionRequest) -> ApiResponse:
    store = _store()
    owner_key = request.owner_key

    if not request.confirmed:
        draft = _create_draft_for_existing_schedule(
            store=store,
            owner_key=owner_key,
            schedule_id=schedule_id,
            action="run",
            payload={},
        )
        return _draft_response(draft)

    result = _execute_schedule_action(
        store=store,
        draft=_build_existing_schedule_action(owner_key=owner_key, schedule_id=schedule_id, action="run", payload={}),
    )
    if bool(result.get("wake")):
        await _wake_scheduler()
    return ApiResponse(success=True, message="Schedule queued", data={"confirmed": True, **_result_data(result)})


@router.post("/{schedule_id}/pause", response_model=ApiResponse)
async def pause_schedule(schedule_id: str, request: ScheduleOwnerRequest) -> ApiResponse:
    schedule = _set_schedule_status(schedule_id=schedule_id, owner_key=request.owner_key, status="paused")
    return ApiResponse(success=True, message="Schedule paused", data={"schedule": schedule})


@router.post("/{schedule_id}/resume", response_model=ApiResponse)
async def resume_schedule(schedule_id: str, request: ScheduleOwnerRequest) -> ApiResponse:
    schedule = _set_schedule_status(schedule_id=schedule_id, owner_key=request.owner_key, status="active")
    await _wake_scheduler()
    return ApiResponse(success=True, message="Schedule resumed", data={"schedule": schedule})


@router.get("/{schedule_id}/runs", response_model=ApiResponse)
async def list_schedule_runs(schedule_id: str, owner_key: OwnerKey = Query(...), limit: int = Query(default=20, ge=1, le=200)) -> ApiResponse:
    store = _store()
    schedule = store.get_schedule(schedule_id=schedule_id, owner_key=owner_key)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    runs = store.list_runs(schedule_id=schedule_id, owner_key=owner_key, limit=limit)
    return ApiResponse(success=True, message="ok", data={"runs": runs, "count": len(runs)})


@router.post("/drafts/{draft_id}/confirm", response_model=ApiResponse)
async def confirm_draft(draft_id: str, request: DraftConfirmRequest) -> ApiResponse:
    store = _store()
    owner_key = request.owner_key
    draft = store.get_draft(owner_key=owner_key, draft_id=draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found or expired")

    result = _execute_schedule_action(store=store, draft=draft, fallback_meta={"assistant_id": "lead_agent"})
    store.delete_draft(owner_key=owner_key, draft_id=draft_id)
    if bool(result.get("wake")):
        await _wake_scheduler()
    return ApiResponse(success=True, message="Draft confirmed", data={"confirmed": True, **_result_data(result)})
