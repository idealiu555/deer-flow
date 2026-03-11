"""Async checkpointer factory.

Provides an **async context manager** for long-running async servers that need
proper resource cleanup.

Supported backends: memory, sqlite, postgres.

Usage (e.g. FastAPI lifespan)::

    from src.agents.checkpointer.async_provider import make_checkpointer

    async with make_checkpointer() as checkpointer:
        app.state.checkpointer = checkpointer  # InMemorySaver if not configured

For sync usage see :mod:`src.agents.checkpointer.provider`.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import AsyncIterator

from langgraph.types import Checkpointer

from src.agents.checkpointer.provider import (
    POSTGRES_CONN_REQUIRED,
    POSTGRES_INSTALL,
    SQLITE_INSTALL,
    _resolve_sqlite_conn_str,
)
from src.config.app_config import get_app_config

logger = logging.getLogger(__name__)


class EnhancedAsyncSqliteSaver:
    """AsyncSqliteSaver wrapper with thread maintenance helpers."""

    def __init__(self, saver):
        self._saver = saver

    def __getattr__(self, name):
        return getattr(self._saver, name)

    async def adelete_thread(self, thread_id: str) -> None:
        await self._saver.adelete_thread(str(thread_id))

    async def adelete_for_runs(self, run_ids) -> None:
        run_ids = {str(run_id) for run_id in run_ids}
        if not run_ids:
            return

        await self._saver.setup()
        async with self._saver.lock, self._saver.conn.cursor() as cur:
            await cur.execute(
                "SELECT thread_id, checkpoint_ns, checkpoint_id, metadata FROM checkpoints"
            )
            rows_to_delete: list[tuple[str, str, str]] = []
            async for thread_id, checkpoint_ns, checkpoint_id, metadata in cur:
                if not metadata:
                    continue
                try:
                    parsed_metadata = json.loads(metadata)
                except (TypeError, json.JSONDecodeError):
                    continue
                if str(parsed_metadata.get("run_id", "")) in run_ids:
                    rows_to_delete.append(
                        (str(thread_id), str(checkpoint_ns), str(checkpoint_id))
                    )

            if not rows_to_delete:
                return

            await cur.executemany(
                "DELETE FROM writes WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?",
                rows_to_delete,
            )
            await cur.executemany(
                "DELETE FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?",
                rows_to_delete,
            )
            await self._saver.conn.commit()

    async def acopy_thread(self, source_thread_id: str, target_thread_id: str) -> None:
        await self._saver.setup()
        source_thread_id = str(source_thread_id)
        target_thread_id = str(target_thread_id)
        if source_thread_id == target_thread_id:
            return

        async with self._saver.lock, self._saver.conn.cursor() as cur:
            await cur.execute("DELETE FROM writes WHERE thread_id = ?", (target_thread_id,))
            await cur.execute(
                "DELETE FROM checkpoints WHERE thread_id = ?",
                (target_thread_id,),
            )
            await cur.execute(
                """
                SELECT checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata
                FROM checkpoints
                WHERE thread_id = ?
                ORDER BY checkpoint_id ASC
                """,
                (source_thread_id,),
            )
            checkpoints = await cur.fetchall()
            if not checkpoints:
                await self._saver.conn.commit()
                return

            rewritten = []
            for checkpoint_ns, checkpoint_id, parent_checkpoint_id, type_, checkpoint, metadata in checkpoints:
                parsed_metadata = json.loads(metadata) if metadata else {}
                if "thread_id" in parsed_metadata:
                    parsed_metadata["thread_id"] = target_thread_id
                rewritten.append(
                    (
                        target_thread_id,
                        checkpoint_ns,
                        checkpoint_id,
                        parent_checkpoint_id,
                        type_,
                        checkpoint,
                        json.dumps(parsed_metadata, ensure_ascii=False).encode("utf-8"),
                    )
                )

            await cur.executemany(
                """
                INSERT OR REPLACE INTO checkpoints
                (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rewritten,
            )
            await cur.execute(
                """
                INSERT OR REPLACE INTO writes
                (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value)
                SELECT ?, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value
                FROM writes
                WHERE thread_id = ?
                """,
                (target_thread_id, source_thread_id),
            )
            await self._saver.conn.commit()

    async def aprune(self, thread_ids, *, strategy: str = "keep_latest") -> None:
        await self._saver.setup()

        if strategy == "delete_all":
            for thread_id in thread_ids:
                await self._saver.adelete_thread(str(thread_id))
            return

        if strategy != "keep_latest":
            raise ValueError(f"Unsupported prune strategy: {strategy}")

        async with self._saver.lock, self._saver.conn.cursor() as cur:
            for thread_id in map(str, thread_ids):
                await cur.execute(
                    """
                    SELECT checkpoint_ns, MAX(checkpoint_id)
                    FROM checkpoints
                    WHERE thread_id = ?
                    GROUP BY checkpoint_ns
                    """,
                    (thread_id,),
                )
                keep_rows = {
                    (thread_id, str(checkpoint_ns), str(checkpoint_id))
                    for checkpoint_ns, checkpoint_id in await cur.fetchall()
                }

                await cur.execute(
                    "SELECT thread_id, checkpoint_ns, checkpoint_id FROM checkpoints WHERE thread_id = ?",
                    (thread_id,),
                )
                rows_to_delete = [
                    (str(tid), str(ns), str(cid))
                    for tid, ns, cid in await cur.fetchall()
                    if (str(tid), str(ns), str(cid)) not in keep_rows
                ]
                if rows_to_delete:
                    await cur.executemany(
                        "DELETE FROM writes WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?",
                        rows_to_delete,
                    )
                    await cur.executemany(
                        "DELETE FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?",
                        rows_to_delete,
                    )
                if keep_rows:
                    await cur.executemany(
                        """
                        UPDATE checkpoints
                        SET parent_checkpoint_id = NULL
                        WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
                        """,
                        list(keep_rows),
                    )

            await self._saver.conn.commit()

# ---------------------------------------------------------------------------
# Async factory
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _async_checkpointer(config) -> AsyncIterator[Checkpointer]:
    """Async context manager that constructs and tears down a checkpointer."""
    if config.type == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        yield InMemorySaver()
        return

    if config.type == "sqlite":
        try:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        except ImportError as exc:
            raise ImportError(SQLITE_INSTALL) from exc

        import pathlib

        conn_str = _resolve_sqlite_conn_str(config.connection_string or "store.db")
        # Only create parent directories for real filesystem paths
        if conn_str != ":memory:" and not conn_str.startswith("file:"):
            pathlib.Path(conn_str).parent.mkdir(parents=True, exist_ok=True)
        async with AsyncSqliteSaver.from_conn_string(conn_str) as saver:
            await saver.setup()
            yield EnhancedAsyncSqliteSaver(saver)
        return

    if config.type == "postgres":
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        except ImportError as exc:
            raise ImportError(POSTGRES_INSTALL) from exc

        if not config.connection_string:
            raise ValueError(POSTGRES_CONN_REQUIRED)

        async with AsyncPostgresSaver.from_conn_string(config.connection_string) as saver:
            await saver.setup()
            yield saver
        return

    raise ValueError(f"Unknown checkpointer type: {config.type!r}")


# ---------------------------------------------------------------------------
# Public async context manager
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def make_checkpointer() -> AsyncIterator[Checkpointer]:
    """Async context manager that yields a checkpointer for the caller's lifetime.
    Resources are opened on enter and closed on exit — no global state::

        async with make_checkpointer() as checkpointer:
            app.state.checkpointer = checkpointer

    Yields an ``InMemorySaver`` when no checkpointer is configured in *config.yaml*.
    """

    config = get_app_config()

    if config.checkpointer is None:
        from langgraph.checkpoint.memory import InMemorySaver

        yield InMemorySaver()
        return

    async with _async_checkpointer(config.checkpointer) as saver:
        yield saver
