"""SQLite-backed persistence for scheduler jobs, runs, and drafts."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from croniter import croniter

from src.config.paths import resolve_path
from src.config.scheduler_config import SchedulerConfig


class SchedulerValidationError(ValueError):
    """Raised when schedule payload validation fails."""


class SchedulerStore:
    """Persistence layer for schedules, runs, and confirmation drafts."""

    def __init__(self, config: SchedulerConfig):
        self._config = config
        self._db_path = self._resolve_db_path(config.db_path)
        self._lock = threading.RLock()
        self._ensure_schema()

    @staticmethod
    def _resolve_db_path(raw: str) -> Path:
        path = resolve_path(raw)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    owner_key TEXT NOT NULL,
    owner_channel TEXT,
    owner_user TEXT,
    channel_name TEXT,
    chat_id TEXT,
    topic_id TEXT,
    thread_id TEXT,
    assistant_id TEXT NOT NULL,
    title TEXT NOT NULL,
    prompt TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('cron', 'once')),
    cron_expr TEXT,
    run_at_utc TEXT,
    timezone TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'paused')),
    next_run_at TEXT,
    run_now_pending INTEGER NOT NULL DEFAULT 0,
    config_json TEXT NOT NULL,
    context_json TEXT NOT NULL,
    last_error TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_schedules_owner_status ON schedules(owner_key, status);
CREATE INDEX IF NOT EXISTS idx_schedules_next_run ON schedules(next_run_at);

CREATE TABLE IF NOT EXISTS schedule_runs (
    id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL,
    planned_at TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('running', 'success', 'failed')),
    attempt INTEGER NOT NULL DEFAULT 1,
    error TEXT,
    output TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_schedule_runs_schedule_created ON schedule_runs(schedule_id, created_at DESC);

CREATE TABLE IF NOT EXISTS schedule_drafts (
    id TEXT PRIMARY KEY,
    owner_key TEXT NOT NULL,
    action TEXT NOT NULL,
    schedule_id TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_schedule_drafts_owner ON schedule_drafts(owner_key, created_at DESC);
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(schedules)").fetchall()}
            if "run_now_pending" not in columns:
                conn.execute("ALTER TABLE schedules ADD COLUMN run_now_pending INTEGER NOT NULL DEFAULT 0")

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _dt_to_str(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.astimezone(UTC).isoformat()

    @staticmethod
    def _str_to_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        text = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)

    @staticmethod
    def _ensure_timezone(value: str, default_timezone: str) -> str:
        tz_name = (value or default_timezone).strip()
        try:
            ZoneInfo(tz_name)
        except Exception as exc:  # pragma: no cover - zoneinfo type details vary by platform
            raise SchedulerValidationError(f"Invalid timezone: {tz_name}") from exc
        return tz_name

    def _compute_next_run(self, *, kind: str, timezone_name: str, cron_expr: str | None, run_at_utc: str | None, after_utc: datetime | None = None) -> datetime | None:
        base = after_utc or self._utcnow()
        if kind == "once":
            run_at = self._str_to_dt(run_at_utc)
            if run_at is None:
                return None
            return run_at if run_at > base else None

        if not cron_expr:
            return None

        tz = ZoneInfo(timezone_name)
        base_local = base.astimezone(tz)
        itr = croniter(cron_expr, base_local)
        next_local = itr.get_next(datetime)
        if next_local.tzinfo is None:
            next_local = next_local.replace(tzinfo=tz)
        return next_local.astimezone(UTC)

    @classmethod
    def _parse_at_to_utc_str(cls, at: str, timezone_name: str) -> str:
        if not at or not isinstance(at, str):
            raise SchedulerValidationError("schedule.at is required for once schedules")
        text = at.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise SchedulerValidationError(f"Invalid schedule.at: {at}") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(timezone_name))
        return cls._dt_to_str(dt.astimezone(UTC)) or ""

    def normalize_schedule_payload(self, payload: Mapping[str, Any], *, require_prompt: bool = True) -> dict[str, Any]:
        title = str(payload.get("title") or "").strip()
        prompt = str(payload.get("prompt") or "").strip()
        kind = str(payload.get("kind") or "").strip().lower()
        timezone_name = self._ensure_timezone(str(payload.get("timezone") or ""), self._config.default_timezone)

        if not title:
            raise SchedulerValidationError("schedule.title is required")
        if require_prompt and not prompt:
            raise SchedulerValidationError("schedule.prompt is required")
        if kind not in {"cron", "once"}:
            raise SchedulerValidationError("schedule.kind must be 'cron' or 'once'")

        cron_expr: str | None = None
        run_at_utc: str | None = None

        if kind == "cron":
            cron_expr = str(payload.get("cron") or payload.get("cron_expr") or "").strip()
            if not cron_expr:
                raise SchedulerValidationError("schedule.cron is required for cron schedules")
            if not croniter.is_valid(cron_expr):
                raise SchedulerValidationError(f"Invalid cron expression: {cron_expr}")
        else:
            run_at_utc = self._parse_at_to_utc_str(str(payload.get("at") or ""), timezone_name)

        next_run_dt = self._compute_next_run(
            kind=kind,
            timezone_name=timezone_name,
            cron_expr=cron_expr,
            run_at_utc=run_at_utc,
            after_utc=self._utcnow(),
        )
        if next_run_dt is None:
            raise SchedulerValidationError("Schedule has no future run time")

        return {
            "title": title,
            "prompt": prompt,
            "kind": kind,
            "timezone": timezone_name,
            "cron_expr": cron_expr,
            "run_at_utc": run_at_utc,
            "next_run_at": self._dt_to_str(next_run_dt),
        }

    @staticmethod
    def _decode_json(text: str | None) -> dict[str, Any]:
        if not text:
            return {}
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return obj if isinstance(obj, dict) else {}

    @staticmethod
    def _normalize_owner_filter(owner_key: str | None) -> str | None:
        if owner_key is None:
            return None
        normalized = str(owner_key).strip()
        if not normalized:
            raise SchedulerValidationError("owner_key must be non-empty when provided")
        return normalized

    @staticmethod
    def _normalize_status_filter(status: str | None) -> str | None:
        if status is None:
            return None
        normalized = str(status).strip()
        if not normalized:
            return None
        if normalized not in {"active", "paused"}:
            raise SchedulerValidationError("status filter must be active or paused")
        return normalized

    def _row_to_schedule(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "owner_key": row["owner_key"],
            "owner_channel": row["owner_channel"],
            "owner_user": row["owner_user"],
            "channel_name": row["channel_name"],
            "chat_id": row["chat_id"],
            "topic_id": row["topic_id"],
            "thread_id": row["thread_id"],
            "assistant_id": row["assistant_id"],
            "title": row["title"],
            "prompt": row["prompt"],
            "kind": row["kind"],
            "cron": row["cron_expr"],
            "at": row["run_at_utc"],
            "timezone": row["timezone"],
            "status": row["status"],
            "next_run_at": row["next_run_at"],
            "last_error": row["last_error"],
            "run_now_pending": bool(row["run_now_pending"]),
            "config": self._decode_json(row["config_json"]),
            "context": self._decode_json(row["context_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "schedule_id": row["schedule_id"],
            "planned_at": row["planned_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "status": row["status"],
            "attempt": row["attempt"],
            "error": row["error"],
            "output": row["output"],
            "created_at": row["created_at"],
        }

    def _purge_expired_drafts(self, conn: sqlite3.Connection) -> None:
        now_s = self._dt_to_str(self._utcnow())
        conn.execute("DELETE FROM schedule_drafts WHERE expires_at <= ?", (now_s,))

    def create_draft(self, *, owner_key: str, action: str, payload: dict[str, Any], schedule_id: str | None = None) -> dict[str, Any]:
        now = self._utcnow()
        draft = {
            "id": uuid.uuid4().hex,
            "owner_key": owner_key,
            "action": action,
            "schedule_id": schedule_id,
            "payload": payload,
            "created_at": self._dt_to_str(now),
            "expires_at": self._dt_to_str(now + timedelta(seconds=self._config.draft_ttl_seconds)),
        }
        with self._lock, self._connect() as conn:
            self._purge_expired_drafts(conn)
            conn.execute(
                """
                INSERT INTO schedule_drafts (id, owner_key, action, schedule_id, payload_json, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft["id"],
                    draft["owner_key"],
                    draft["action"],
                    draft["schedule_id"],
                    json.dumps(draft["payload"], ensure_ascii=False),
                    draft["created_at"],
                    draft["expires_at"],
                ),
            )
        return draft

    def _draft_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "owner_key": row["owner_key"],
            "action": row["action"],
            "schedule_id": row["schedule_id"],
            "payload": self._decode_json(row["payload_json"]),
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
        }

    def get_draft(self, *, owner_key: str, draft_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            self._purge_expired_drafts(conn)
            row = conn.execute("SELECT * FROM schedule_drafts WHERE id = ? AND owner_key = ?", (draft_id, owner_key)).fetchone()
            if row is None:
                return None
        return self._draft_from_row(row)

    def consume_draft(self, *, owner_key: str, draft_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            self._purge_expired_drafts(conn)
            row = conn.execute("SELECT * FROM schedule_drafts WHERE id = ? AND owner_key = ?", (draft_id, owner_key)).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM schedule_drafts WHERE id = ?", (draft_id,))
        return self._draft_from_row(row)

    def create_schedule(
        self,
        *,
        owner_key: str,
        owner_channel: str | None,
        owner_user: str | None,
        channel_name: str | None,
        chat_id: str | None,
        topic_id: str | None,
        thread_id: str | None,
        assistant_id: str,
        payload: Mapping[str, Any],
        config: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized = self.normalize_schedule_payload(payload)
        schedule_id = uuid.uuid4().hex
        now_s = self._dt_to_str(self._utcnow())
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO schedules (
                    id, owner_key, owner_channel, owner_user, channel_name, chat_id, topic_id, thread_id,
                    assistant_id, title, prompt, kind, cron_expr, run_at_utc, timezone, status,
                    next_run_at, config_json, context_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
                """,
                (
                    schedule_id,
                    owner_key,
                    owner_channel,
                    owner_user,
                    channel_name,
                    chat_id,
                    topic_id,
                    thread_id,
                    assistant_id,
                    normalized["title"],
                    normalized["prompt"],
                    normalized["kind"],
                    normalized["cron_expr"],
                    normalized["run_at_utc"],
                    normalized["timezone"],
                    normalized["next_run_at"],
                    json.dumps(dict(config), ensure_ascii=False),
                    json.dumps(dict(context), ensure_ascii=False),
                    now_s,
                    now_s,
                ),
            )
        created = self.get_schedule(schedule_id=schedule_id, owner_key=owner_key)
        if created is None:
            raise RuntimeError("Failed to load created schedule")
        return created

    def get_schedule(self, *, schedule_id: str, owner_key: str | None = None) -> dict[str, Any] | None:
        sql = "SELECT * FROM schedules WHERE id = ?"
        params: list[Any] = [schedule_id]
        if owner_key is not None:
            sql += " AND owner_key = ?"
            params.append(owner_key)
        with self._lock, self._connect() as conn:
            row = conn.execute(sql, tuple(params)).fetchone()
            return self._row_to_schedule(row) if row is not None else None

    def list_schedules(self, *, owner_key: str | None = None, status: str | None = None, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        owner_filter = self._normalize_owner_filter(owner_key)
        status_filter = self._normalize_status_filter(status)
        sql = "SELECT * FROM schedules"
        clauses: list[str] = []
        params: list[Any] = []
        if owner_filter is not None:
            clauses.append("owner_key = ?")
            params.append(owner_filter)
        if status_filter is not None:
            clauses.append("status = ?")
            params.append(status_filter)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([max(1, min(limit, 200)), max(0, offset)])

        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [self._row_to_schedule(row) for row in rows]

    def update_schedule(self, *, schedule_id: str, owner_key: str, patch: Mapping[str, Any]) -> dict[str, Any] | None:
        current = self.get_schedule(schedule_id=schedule_id, owner_key=owner_key)
        if current is None:
            return None

        title_value = patch.get("title", current["title"])
        if title_value is None:
            title_value = current["title"]
        prompt_value = patch.get("prompt", current["prompt"])
        if prompt_value is None:
            prompt_value = current["prompt"]
        merged_title = str(title_value).strip()
        merged_prompt = str(prompt_value).strip()
        if not merged_title:
            raise SchedulerValidationError("schedule.title is required")
        if not merged_prompt:
            raise SchedulerValidationError("schedule.prompt is required")

        timing_keys = {"kind", "timezone", "cron", "at"}
        need_revalidate = any(key in patch for key in timing_keys)

        if need_revalidate:
            merged_payload = {
                "title": merged_title,
                "prompt": merged_prompt,
                "kind": patch.get("kind", current["kind"]),
                "timezone": patch.get("timezone", current["timezone"]),
                "cron": patch.get("cron", current.get("cron")),
                "at": patch.get("at", current.get("at")),
            }
            normalized = self.normalize_schedule_payload(merged_payload)
        else:
            normalized = {
                "title": merged_title,
                "prompt": merged_prompt,
                "kind": current["kind"],
                "timezone": current["timezone"],
                "cron_expr": current.get("cron"),
                "run_at_utc": current.get("at"),
                "next_run_at": current.get("next_run_at"),
            }

        status = str(patch.get("status") or current["status"])
        if status not in {"active", "paused"}:
            raise SchedulerValidationError("status must be active or paused")

        next_run_at: str | None
        if status == "active":
            if need_revalidate:
                next_run_at = normalized["next_run_at"]
            elif current["status"] != "active":
                next_dt = self._compute_next_run(
                    kind=normalized["kind"],
                    timezone_name=normalized["timezone"],
                    cron_expr=normalized.get("cron_expr"),
                    run_at_utc=normalized.get("run_at_utc"),
                    after_utc=self._utcnow(),
                )
                next_run_at = self._dt_to_str(next_dt)
            else:
                next_run_at = current.get("next_run_at")
            if next_run_at is None:
                raise SchedulerValidationError("Schedule has no future run time")
        else:
            next_run_at = None
        now = self._utcnow()
        now_s = self._dt_to_str(now)

        with self._lock, self._connect() as conn:
            lease_state = self._load_lease_state(conn, schedule_id=schedule_id, owner_key=owner_key, now=now)
            if lease_state is None:
                return None

            lease_owner, lease_expires_at, has_active_lease, run_now_pending = lease_state
            if status == "paused":
                run_now_pending = False
            conn.execute(
                """
                UPDATE schedules
                SET title = ?, prompt = ?, kind = ?, cron_expr = ?, run_at_utc = ?, timezone = ?,
                    status = ?, next_run_at = ?, run_now_pending = ?, lease_owner = ?, lease_expires_at = ?, updated_at = ?
                WHERE id = ? AND owner_key = ?
                """,
                (
                    normalized["title"],
                    normalized["prompt"],
                    normalized["kind"],
                    normalized["cron_expr"],
                    normalized["run_at_utc"],
                    normalized["timezone"],
                    status,
                    next_run_at,
                    int(run_now_pending),
                    lease_owner if has_active_lease else None,
                    lease_expires_at if has_active_lease else None,
                    now_s,
                    schedule_id,
                    owner_key,
                ),
            )

        return self.get_schedule(schedule_id=schedule_id, owner_key=owner_key)

    def set_schedule_status(self, *, schedule_id: str, owner_key: str, status: str) -> dict[str, Any] | None:
        if status not in {"active", "paused"}:
            raise SchedulerValidationError("status must be active or paused")

        current = self.get_schedule(schedule_id=schedule_id, owner_key=owner_key)
        if current is None:
            return None

        now = self._utcnow()
        next_run_dt = None
        if status == "active":
            next_run_dt = self._compute_next_run(
                kind=current["kind"],
                timezone_name=current["timezone"],
                cron_expr=current.get("cron"),
                run_at_utc=current.get("at"),
                after_utc=now,
            )
            if next_run_dt is None:
                raise SchedulerValidationError("Schedule has no future run time and cannot be activated")

        with self._lock, self._connect() as conn:
            lease_state = self._load_lease_state(conn, schedule_id=schedule_id, owner_key=owner_key, now=now)
            if lease_state is None:
                return None
            lease_owner, lease_expires_at, has_active_lease, run_now_pending = lease_state
            if status == "paused":
                run_now_pending = False
            conn.execute(
                """
                UPDATE schedules
                SET status = ?, next_run_at = ?, run_now_pending = ?,
                    lease_owner = ?, lease_expires_at = ?, updated_at = ?
                WHERE id = ? AND owner_key = ?
                """,
                (
                    status,
                    self._dt_to_str(next_run_dt),
                    int(run_now_pending),
                    lease_owner if has_active_lease else None,
                    lease_expires_at if has_active_lease else None,
                    self._dt_to_str(now),
                    schedule_id,
                    owner_key,
                ),
            )

        return self.get_schedule(schedule_id=schedule_id, owner_key=owner_key)

    def _load_lease_state(
        self,
        conn: sqlite3.Connection,
        *,
        schedule_id: str,
        owner_key: str,
        now: datetime,
    ) -> tuple[str | None, str | None, bool, bool] | None:
        row = conn.execute(
            "SELECT lease_owner, lease_expires_at, run_now_pending FROM schedules WHERE id = ? AND owner_key = ?",
            (schedule_id, owner_key),
        ).fetchone()
        if row is None:
            return None
        lease_owner = row["lease_owner"]
        lease_expires_at = row["lease_expires_at"]
        lease_expires_dt = self._str_to_dt(lease_expires_at)
        has_active_lease = bool(lease_owner) and lease_expires_dt is not None and lease_expires_dt > now
        run_now_pending = bool(row["run_now_pending"])
        return lease_owner, lease_expires_at, has_active_lease, run_now_pending

    def delete_schedule(self, *, schedule_id: str, owner_key: str) -> bool:
        with self._lock, self._connect() as conn:
            res = conn.execute("DELETE FROM schedules WHERE id = ? AND owner_key = ?", (schedule_id, owner_key))
            return res.rowcount > 0

    def set_schedule_thread(self, *, schedule_id: str, thread_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE schedules SET thread_id = ?, updated_at = ? WHERE id = ?",
                (thread_id, self._dt_to_str(self._utcnow()), schedule_id),
            )

    def trigger_schedule(self, *, schedule_id: str, owner_key: str) -> dict[str, Any] | None:
        now = self._utcnow()
        now_s = self._dt_to_str(now)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT lease_expires_at FROM schedules WHERE id = ? AND owner_key = ?",
                (schedule_id, owner_key),
            ).fetchone()
            if row is None:
                return None

            lease_expires_at = self._str_to_dt(row["lease_expires_at"])
            has_active_lease = lease_expires_at is not None and lease_expires_at > now

            if has_active_lease:
                conn.execute(
                    """
                    UPDATE schedules
                    SET status = 'active', run_now_pending = 1, updated_at = ?
                    WHERE id = ? AND owner_key = ?
                    """,
                    (now_s, schedule_id, owner_key),
                )
            else:
                conn.execute(
                    """
                    UPDATE schedules
                    SET status = 'active', next_run_at = ?, run_now_pending = 0,
                        lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                    WHERE id = ? AND owner_key = ?
                    """,
                    (now_s, now_s, schedule_id, owner_key),
                )
        return self.get_schedule(schedule_id=schedule_id, owner_key=owner_key)

    def claim_due_schedules(
        self,
        *,
        limit: int,
        lease_owner: str,
        lease_seconds: int,
        include_channel_targets: bool = True,
    ) -> list[dict[str, Any]]:
        now = self._utcnow()
        now_s = self._dt_to_str(now)
        lease_exp = self._dt_to_str(now + timedelta(seconds=max(10, lease_seconds)))
        channel_clause = ""
        if not include_channel_targets:
            channel_clause = " AND (channel_name IS NULL OR TRIM(channel_name) = '' OR chat_id IS NULL OR TRIM(chat_id) = '')"

        claimed_ids: list[str] = []
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                f"""
                SELECT id FROM schedules
                WHERE status = 'active'
                  AND next_run_at IS NOT NULL
                  AND next_run_at <= ?
                  AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                  {channel_clause}
                ORDER BY next_run_at ASC
                LIMIT ?
                """,
                (now_s, now_s, max(1, min(limit, 100))),
            ).fetchall()
            for row in rows:
                schedule_id = row["id"]
                updated = conn.execute(
                    f"""
                    UPDATE schedules
                    SET lease_owner = ?, lease_expires_at = ?, updated_at = ?
                    WHERE id = ?
                      AND status = 'active'
                      AND next_run_at IS NOT NULL
                      AND next_run_at <= ?
                      AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                      {channel_clause}
                    """,
                    (lease_owner, lease_exp, now_s, schedule_id, now_s, now_s),
                )
                if updated.rowcount > 0:
                    claimed_ids.append(schedule_id)
            conn.commit()

        if not claimed_ids:
            return []

        with self._lock, self._connect() as conn:
            sql = "SELECT * FROM schedules WHERE id IN ({})".format(
                ",".join("?" for _ in claimed_ids)
            )
            rows = conn.execute(sql, tuple(claimed_ids)).fetchall()
            by_id = {row["id"]: self._row_to_schedule(row) for row in rows}
            return [by_id[sid] for sid in claimed_ids if sid in by_id]

    def renew_schedule_lease(self, *, schedule_id: str, lease_owner: str, lease_seconds: int) -> bool:
        now = self._utcnow()
        lease_exp = self._dt_to_str(now + timedelta(seconds=max(10, lease_seconds)))
        now_s = self._dt_to_str(now)
        with self._lock, self._connect() as conn:
            updated = conn.execute(
                """
                UPDATE schedules
                SET lease_expires_at = ?, updated_at = ?
                WHERE id = ? AND lease_owner = ?
                """,
                (lease_exp, now_s, schedule_id, lease_owner),
            )
            return updated.rowcount > 0

    def release_schedule_claim(self, *, schedule_id: str, lease_owner: str, success: bool, error: str | None = None) -> dict[str, Any] | None:
        now = self._utcnow()
        now_s = self._dt_to_str(now)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM schedules WHERE id = ? AND lease_owner = ?",
                (schedule_id, lease_owner),
            ).fetchone()
            if row is None:
                return None

            current = self._row_to_schedule(row)
            if current["status"] == "paused":
                next_run_dt = None
                status = "paused"
            elif current["run_now_pending"]:
                next_run_dt = now
                status = "active"
            elif current["kind"] == "once":
                next_run_dt = None
                status = "paused"
            else:
                next_run_dt = self._compute_next_run(
                    kind=current["kind"],
                    timezone_name=current["timezone"],
                    cron_expr=current.get("cron"),
                    run_at_utc=current.get("at"),
                    after_utc=now,
                )
                status = "active"

            updated = conn.execute(
                """
                UPDATE schedules
                SET status = ?, next_run_at = ?, last_error = ?,
                    lease_owner = NULL, lease_expires_at = NULL, run_now_pending = 0, updated_at = ?
                WHERE id = ? AND lease_owner = ?
                """,
                (
                    status,
                    self._dt_to_str(next_run_dt),
                    None if success else (error or "Execution failed"),
                    now_s,
                    schedule_id,
                    lease_owner,
                ),
            )
            if updated.rowcount == 0:
                return None

        return self.get_schedule(schedule_id=schedule_id)

    def create_run(self, *, schedule_id: str, planned_at: str | None, attempt: int) -> dict[str, Any]:
        now_s = self._dt_to_str(self._utcnow())
        run_id = uuid.uuid4().hex
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO schedule_runs (id, schedule_id, planned_at, started_at, status, attempt, created_at)
                VALUES (?, ?, ?, ?, 'running', ?, ?)
                """,
                (run_id, schedule_id, planned_at, now_s, attempt, now_s),
            )
        created = self.get_run(run_id=run_id)
        if created is None:
            raise RuntimeError("Failed to load created run")
        return created

    def get_run(self, *, run_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM schedule_runs WHERE id = ?", (run_id,)).fetchone()
            return self._row_to_run(row) if row is not None else None

    def finish_run(self, *, run_id: str, status: str, error: str | None = None, output: str | None = None) -> dict[str, Any] | None:
        if status not in {"success", "failed"}:
            raise SchedulerValidationError("run status must be success or failed")
        finished_at = self._dt_to_str(self._utcnow())
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE schedule_runs
                SET status = ?, finished_at = ?, error = ?, output = ?
                WHERE id = ?
                """,
                (status, finished_at, error, output, run_id),
            )
            row = conn.execute("SELECT * FROM schedule_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                return None
            schedule_id = row["schedule_id"]
            self._trim_runs_locked(conn, schedule_id=schedule_id)
            return self._row_to_run(row)

    def _trim_runs_locked(self, conn: sqlite3.Connection, *, schedule_id: str) -> None:
        conn.execute(
            """
            DELETE FROM schedule_runs
            WHERE schedule_id = ?
              AND id NOT IN (
                SELECT id FROM schedule_runs
                WHERE schedule_id = ?
                ORDER BY created_at DESC
                LIMIT ?
              )
            """,
            (schedule_id, schedule_id, self._config.max_runs_per_schedule),
        )

    def list_runs(self, *, schedule_id: str, owner_key: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        owner_filter = self._normalize_owner_filter(owner_key)
        if owner_filter is not None:
            schedule = self.get_schedule(schedule_id=schedule_id, owner_key=owner_filter)
            if schedule is None:
                return []
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM schedule_runs
                WHERE schedule_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (schedule_id, max(1, min(limit, 200))),
            ).fetchall()
            return [self._row_to_run(row) for row in rows]

    def get_status(self) -> dict[str, Any]:
        now_s = self._dt_to_str(self._utcnow())
        with self._lock, self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM schedules").fetchone()[0]
            active = conn.execute("SELECT COUNT(*) FROM schedules WHERE status = 'active'").fetchone()[0]
            paused = conn.execute("SELECT COUNT(*) FROM schedules WHERE status = 'paused'").fetchone()[0]
            due = conn.execute(
                "SELECT COUNT(*) FROM schedules WHERE status='active' AND next_run_at IS NOT NULL AND next_run_at <= ?",
                (now_s,),
            ).fetchone()[0]
            running = conn.execute("SELECT COUNT(*) FROM schedule_runs WHERE status='running'").fetchone()[0]
            drafts = conn.execute("SELECT COUNT(*) FROM schedule_drafts WHERE expires_at > ?", (now_s,)).fetchone()[0]

        return {
            "enabled": self._config.enabled,
            "db_path": str(self._db_path),
            "total": int(total),
            "active": int(active),
            "paused": int(paused),
            "due": int(due),
            "running": int(running),
            "drafts": int(drafts),
        }


def derive_owner_identity(owner_key: str) -> tuple[str, str | None]:
    """Parse scheduler owner key into stable owner channel/user tuple."""
    key = str(owner_key or "").strip() or "web:settings"
    parsed_channel, parsed_user = key.split(":", 1) if ":" in key else ("web", key)
    owner_channel = parsed_channel.strip() or "web"
    owner_user = parsed_user.strip() or None
    return owner_channel, owner_user


def resolve_owner_from_context(context: Mapping[str, Any] | None) -> dict[str, str | None]:
    """Resolve scheduler owner scope from runtime context."""
    ctx = dict(context or {})
    channel_name = str(ctx.get("channel_name") or "").strip() or None
    user_id = str(ctx.get("user_id") or "").strip() or None
    thread_id = str(ctx.get("thread_id") or "").strip() or None

    if channel_name and user_id:
        owner_key = f"{channel_name}:{user_id}"
        return {
            "owner_key": owner_key,
            "owner_channel": channel_name,
            "owner_user": user_id,
            "channel_name": channel_name,
            "chat_id": str(ctx.get("chat_id") or "").strip() or None,
            "topic_id": str(ctx.get("topic_id") or "").strip() or None,
            "thread_id": thread_id,
        }

    fallback_key = str(ctx.get("owner_key") or "").strip() or "web:settings"
    owner_channel, owner_user = derive_owner_identity(fallback_key)
    return {
        "owner_key": fallback_key,
        "owner_channel": owner_channel,
        "owner_user": owner_user,
        "channel_name": None,
        "chat_id": None,
        "topic_id": None,
        "thread_id": thread_id,
    }


_store: SchedulerStore | None = None
_store_key: tuple[Any, ...] | None = None
_store_lock = threading.Lock()


def _store_config_key(config: SchedulerConfig) -> tuple[Any, ...]:
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


def get_scheduler_store(config: SchedulerConfig) -> SchedulerStore:
    """Return singleton scheduler store."""
    global _store, _store_key
    key = _store_config_key(config)
    if _store is not None and _store_key == key:
        return _store
    with _store_lock:
        key = _store_config_key(config)
        if _store is None or _store_key != key:
            _store = SchedulerStore(config)
            _store_key = key
    return _store
