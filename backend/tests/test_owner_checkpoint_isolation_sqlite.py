"""Real AsyncSqliteSaver coverage for owner-qualified checkpoint isolation."""

from __future__ import annotations

import asyncio
from pathlib import Path

from langgraph.checkpoint.base import empty_checkpoint, uuid6
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from deerflow.runtime.checkpoint_owner import owner_checkpoint_config, owner_checkpoint_thread_id


def _checkpoint(marker: str) -> dict:
    checkpoint = empty_checkpoint()
    checkpoint["id"] = str(uuid6())
    checkpoint["channel_values"] = {"marker": marker}
    checkpoint["channel_versions"] = {"marker": "1"}
    return checkpoint


def test_owner_checkpoint_thread_id_is_unambiguous_and_stable():
    assert owner_checkpoint_thread_id("bc", "a") != owner_checkpoint_thread_id("c", "ab")
    assert owner_checkpoint_thread_id("thread", "owner") == owner_checkpoint_thread_id("thread", "owner")
    assert "thread" not in owner_checkpoint_thread_id("thread", "owner")
    assert "owner" not in owner_checkpoint_thread_id("thread", "owner")


def test_async_sqlite_saver_owner_qualified_reads_and_writes_are_isolated(tmp_path: Path):
    async def scenario() -> None:
        db_path = tmp_path / "checkpoints.sqlite"
        async with AsyncSqliteSaver.from_conn_string(str(db_path)) as saver:
            config_a = owner_checkpoint_config("shared-thread", "owner-a", checkpoint_ns="")
            config_b = owner_checkpoint_config("shared-thread", "owner-b", checkpoint_ns="")
            legacy_config = {"configurable": {"thread_id": "shared-thread", "checkpoint_ns": ""}}

            await saver.aput(config_a, _checkpoint("A"), {"source": "loop", "step": 1}, {"marker": "1"})
            await saver.aput(config_b, _checkpoint("B"), {"source": "loop", "step": 1}, {"marker": "1"})
            await saver.aput(legacy_config, _checkpoint("legacy"), {"source": "loop", "step": 1}, {"marker": "1"})

            tuple_a = await saver.aget_tuple(config_a)
            tuple_b = await saver.aget_tuple(config_b)
            assert tuple_a is not None
            assert tuple_b is not None
            assert tuple_a.checkpoint["channel_values"]["marker"] == "A"
            assert tuple_b.checkpoint["channel_values"]["marker"] == "B"

            listed_a = [item async for item in saver.alist(config_a)]
            listed_b = [item async for item in saver.alist(config_b)]
            assert [item.checkpoint["channel_values"]["marker"] for item in listed_a] == ["A"]
            assert [item.checkpoint["channel_values"]["marker"] for item in listed_b] == ["B"]

            # No bare-thread fallback: an existing legacy checkpoint stays invisible.
            assert await saver.aget_tuple(config_a) is not None
            assert await saver.aget_tuple(config_b) is not None
            assert (await saver.aget_tuple(config_a)).checkpoint["channel_values"]["marker"] != "legacy"
            assert (await saver.aget_tuple(config_b)).checkpoint["channel_values"]["marker"] != "legacy"

    asyncio.run(scenario())
