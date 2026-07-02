import pytest

from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal


class _DumpableMessage:
    def model_dump(self):
        return {"type": "ai", "id": "msg-1", "content": "working"}


@pytest.mark.asyncio
async def test_task_event_is_persisted_as_run_message():
    store = MemoryRunEventStore()
    journal = RunJournal("run-1", "thread-1", store, flush_threshold=100)

    journal.record_task_event(
        {
            "type": "task_running",
            "task_id": "call-1",
            "thread_id": "thread-1",
            "run_id": "run-1",
            "message": _DumpableMessage(),
        }
    )
    await journal.flush()

    [row] = await store.list_messages_by_run("thread-1", "run-1")
    assert row["event_type"] == "task_running"
    assert row["category"] == "message"
    assert row["metadata"] == {
        "caller": "task_event",
        "thread_id": "thread-1",
        "run_id": "run-1",
        "task_id": "call-1",
    }
    assert row["content"]["message"] == {"type": "ai", "id": "msg-1", "content": "working"}


@pytest.mark.asyncio
async def test_task_event_without_complete_identity_is_not_persisted():
    store = MemoryRunEventStore()
    journal = RunJournal("run-1", "thread-1", store, flush_threshold=100)

    journal.record_task_event(
        {
            "type": "task_running",
            "task_id": "call-1",
            "thread_id": "thread-1",
        }
    )
    await journal.flush()

    assert await store.list_messages_by_run("thread-1", "run-1") == []


@pytest.mark.asyncio
async def test_task_event_with_mismatched_identity_is_not_persisted():
    store = MemoryRunEventStore()
    journal = RunJournal("run-1", "thread-1", store, flush_threshold=100)

    journal.record_task_event(
        {
            "type": "task_running",
            "task_id": "call-1",
            "thread_id": "thread-2",
            "run_id": "run-1",
        }
    )
    journal.record_task_event(
        {
            "type": "task_running",
            "task_id": "call-1",
            "thread_id": "thread-1",
            "run_id": "run-2",
        }
    )
    await journal.flush()

    assert await store.list_messages_by_run("thread-1", "run-1") == []
