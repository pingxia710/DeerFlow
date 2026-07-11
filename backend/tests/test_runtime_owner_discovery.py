import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from deerflow.persistence.round_state import MemoryRoundStateStore, RoundStateRepository
from deerflow.persistence.round_state.model import RoundEventRow, TaskLaneRow
from deerflow.persistence.run import RunRepository
from deerflow.runtime.events.store.db import DbRunEventStore
from deerflow.runtime.events.store.jsonl import JsonlRunEventStore
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.runs.store.memory import MemoryRunStore
from deerflow.runtime.user_context import DEFAULT_USER_ID


async def _seed_owner_markers(run_store, event_store, round_store) -> None:
    for index, owner in enumerate((None, DEFAULT_USER_ID, "owner-b")):
        await run_store.put(f"run-{index}", thread_id="thread-owners", user_id=owner)
        await event_store.put(
            thread_id="thread-owners",
            run_id=f"run-{index}",
            event_type="run.start",
            category="trace",
            user_id=owner,
        )
        await round_store.bind_run(
            thread_id="thread-owners",
            run_id=f"run-{index}",
            user_id=owner,
        )


async def _assert_owner_markers(run_store, event_store, round_store) -> None:
    expected = {None, DEFAULT_USER_ID, "owner-b"}
    assert await run_store.list_owners_by_thread("thread-owners") == expected
    assert await event_store.list_owners_by_thread("thread-owners") == expected
    assert await round_store.list_owners_by_thread("thread-owners") == expected
    assert await run_store.list_owners_by_thread("missing") == set()
    assert await event_store.list_owners_by_thread("missing") == set()
    assert await round_store.list_owners_by_thread("missing") == set()


@pytest.mark.anyio
async def test_memory_stores_list_all_thread_owner_markers():
    stores = (MemoryRunStore(), MemoryRunEventStore(), MemoryRoundStateStore())
    await _seed_owner_markers(*stores)
    await _assert_owner_markers(*stores)
    stores[2].events["orphan"] = [{"thread_id": "thread-owners", "user_id": "event-owner"}]
    stores[2].task_lanes[("thread-owners", "orphan-run", "orphan-task")] = {
        "thread_id": "thread-owners",
        "user_id": "lane-owner",
    }
    assert await stores[2].list_owners_by_thread("thread-owners") == {
        None,
        DEFAULT_USER_ID,
        "owner-b",
        "event-owner",
        "lane-owner",
    }


@pytest.mark.anyio
async def test_sql_stores_list_all_thread_owner_markers(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'owners.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    stores = (
        RunRepository(session_factory),
        DbRunEventStore(session_factory),
        RoundStateRepository(session_factory),
    )
    try:
        await _seed_owner_markers(*stores)
        await _assert_owner_markers(*stores)
        async with session_factory() as session:
            session.add(
                RoundEventRow(
                    round_id="orphan-round",
                    thread_id="thread-owners",
                    user_id="event-owner",
                    event_type="orphan",
                    seq=1,
                )
            )
            session.add(
                TaskLaneRow(
                    thread_id="thread-owners",
                    run_id="orphan-run",
                    task_id="orphan-task",
                    round_id="orphan-round",
                    user_id="lane-owner",
                    status="in_progress",
                )
            )
            await session.commit()
        assert await stores[2].list_owners_by_thread("thread-owners") == {
            None,
            DEFAULT_USER_ID,
            "owner-b",
            "event-owner",
            "lane-owner",
        }
    finally:
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.no_auto_user
async def test_jsonl_event_store_lists_owner_markers_across_layouts(tmp_path):
    store = JsonlRunEventStore(tmp_path)
    for index, owner in enumerate((None, DEFAULT_USER_ID, "owner-b")):
        await store.put(
            thread_id="thread-owners",
            run_id=f"run-{index}",
            event_type="run.start",
            category="trace",
            user_id=owner,
        )

    assert await store.list_owners_by_thread("thread-owners") == {
        None,
        DEFAULT_USER_ID,
        "owner-b",
    }
