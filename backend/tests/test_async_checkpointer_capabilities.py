import pytest
from langgraph.checkpoint.base import empty_checkpoint

from src.agents.checkpointer.async_provider import EnhancedAsyncSqliteSaver


@pytest.mark.anyio
async def test_adelete_thread_removes_all_checkpoints_and_writes_for_thread(tmp_path):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    db_path = tmp_path / "checkpoints.db"
    async with AsyncSqliteSaver.from_conn_string(str(db_path)) as saver:
        await saver.setup()
        checkpointer = EnhancedAsyncSqliteSaver(saver)

        config = {"configurable": {"thread_id": "thread-delete", "checkpoint_ns": ""}}
        checkpoint = empty_checkpoint()
        stored = await checkpointer.aput(
            config,
            checkpoint,
            {"run_id": "run-delete"},
            checkpoint["channel_versions"],
        )
        await checkpointer.aput_writes(
            stored,
            [("messages", {"value": "delete-me"})],
            "task-delete",
        )

        await checkpointer.adelete_thread("thread-delete")

        remaining = [
            item
            async for item in checkpointer.alist(
                {"configurable": {"thread_id": "thread-delete", "checkpoint_ns": ""}}
            )
        ]

        assert remaining == []


@pytest.mark.anyio
async def test_adelete_for_runs_removes_matching_checkpoints_and_writes(tmp_path):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    db_path = tmp_path / "checkpoints.db"
    async with AsyncSqliteSaver.from_conn_string(str(db_path)) as saver:
        await saver.setup()
        checkpointer = EnhancedAsyncSqliteSaver(saver)

        base_config = {"configurable": {"thread_id": "thread-a", "checkpoint_ns": ""}}

        checkpoint_a = empty_checkpoint()
        stored_a = await checkpointer.aput(
            base_config,
            checkpoint_a,
            {"run_id": "run-a"},
            checkpoint_a["channel_versions"],
        )
        await checkpointer.aput_writes(stored_a, [("messages", {"value": "a"})], "task-a")

        checkpoint_b = empty_checkpoint()
        stored_b = await checkpointer.aput(
            stored_a,
            checkpoint_b,
            {"run_id": "run-b"},
            checkpoint_b["channel_versions"],
        )
        await checkpointer.aput_writes(stored_b, [("messages", {"value": "b"})], "task-b")

        await checkpointer.adelete_for_runs(["run-a"])

        remaining = [
            item
            async for item in checkpointer.alist(
                {"configurable": {"thread_id": "thread-a", "checkpoint_ns": ""}}
            )
        ]

        assert [item.metadata.get("run_id") for item in remaining] == ["run-b"]


@pytest.mark.anyio
async def test_acopy_thread_copies_checkpoints_and_rewrites_metadata_thread_id(tmp_path):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    db_path = tmp_path / "checkpoints.db"
    async with AsyncSqliteSaver.from_conn_string(str(db_path)) as saver:
        await saver.setup()
        checkpointer = EnhancedAsyncSqliteSaver(saver)

        source_config = {"configurable": {"thread_id": "source-thread", "checkpoint_ns": ""}}
        checkpoint = empty_checkpoint()
        stored = await checkpointer.aput(
            source_config,
            checkpoint,
            {"run_id": "run-copy", "thread_id": "source-thread"},
            checkpoint["channel_versions"],
        )
        await checkpointer.aput_writes(stored, [("messages", {"value": "copy-me"})], "task-copy")

        await checkpointer.acopy_thread("source-thread", "target-thread")

        copied = await checkpointer.aget_tuple(
            {"configurable": {"thread_id": "target-thread", "checkpoint_ns": ""}}
        )

        assert copied is not None
        assert copied.metadata["thread_id"] == "target-thread"
        assert copied.metadata["run_id"] == "run-copy"
        assert copied.pending_writes == [("task-copy", "messages", {"value": "copy-me"})]


@pytest.mark.anyio
async def test_acopy_thread_replaces_existing_target_history(tmp_path):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    db_path = tmp_path / "checkpoints.db"
    async with AsyncSqliteSaver.from_conn_string(str(db_path)) as saver:
        await saver.setup()
        checkpointer = EnhancedAsyncSqliteSaver(saver)

        source_config = {"configurable": {"thread_id": "source-thread", "checkpoint_ns": ""}}
        source_checkpoint = empty_checkpoint()
        source_stored = await checkpointer.aput(
            source_config,
            source_checkpoint,
            {"run_id": "run-source"},
            source_checkpoint["channel_versions"],
        )
        await checkpointer.aput_writes(
            source_stored,
            [("messages", {"value": "source"})],
            "task-source",
        )

        target_config = {"configurable": {"thread_id": "target-thread", "checkpoint_ns": ""}}
        target_checkpoint = empty_checkpoint()
        target_stored = await checkpointer.aput(
            target_config,
            target_checkpoint,
            {"run_id": "run-target"},
            target_checkpoint["channel_versions"],
        )
        await checkpointer.aput_writes(
            target_stored,
            [("messages", {"value": "target"})],
            "task-target",
        )

        await checkpointer.acopy_thread("source-thread", "target-thread")

        copied_rows = [
            item
            async for item in checkpointer.alist(
                {"configurable": {"thread_id": "target-thread", "checkpoint_ns": ""}}
            )
        ]

        assert len(copied_rows) == 1
        assert copied_rows[0].metadata["run_id"] == "run-source"
        assert copied_rows[0].pending_writes == [
            ("task-source", "messages", {"value": "source"})
        ]


@pytest.mark.anyio
async def test_aprune_keep_latest_keeps_only_latest_checkpoint_per_namespace(tmp_path):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    db_path = tmp_path / "checkpoints.db"
    async with AsyncSqliteSaver.from_conn_string(str(db_path)) as saver:
        await saver.setup()
        checkpointer = EnhancedAsyncSqliteSaver(saver)

        config = {"configurable": {"thread_id": "thread-prune", "checkpoint_ns": ""}}

        first = empty_checkpoint()
        stored_first = await checkpointer.aput(
            config,
            first,
            {"run_id": "run-1"},
            first["channel_versions"],
        )

        second = empty_checkpoint()
        await checkpointer.aput(
            stored_first,
            second,
            {"run_id": "run-2"},
            second["channel_versions"],
        )

        await checkpointer.aprune(["thread-prune"], strategy="keep_latest")

        remaining = [
            item
            async for item in checkpointer.alist(
                {"configurable": {"thread_id": "thread-prune", "checkpoint_ns": ""}}
            )
        ]

        assert len(remaining) == 1
        assert remaining[0].metadata["run_id"] == "run-2"
        assert remaining[0].parent_config is None
