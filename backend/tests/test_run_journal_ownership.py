import pytest

from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal


@pytest.mark.anyio
async def test_run_journal_rejects_task_event_identity_mismatch():
    store = MemoryRunEventStore()
    journal = RunJournal(run_id="run-1", thread_id="thread-1", event_store=store)

    journal.record_task_event({"type": "task.started", "task_id": "task-1", "thread_id": "thread-2", "run_id": "run-1"})
    journal.record_task_event({"type": "task.completed", "task_id": "task-1", "thread_id": "thread-1", "run_id": "run-2"})
    journal.record_task_event({"type": "task.completed", "task_id": "task-1", "thread_id": "thread-1"})
    await journal.flush()

    assert await store.list_events("thread-1", "run-1") == []
    assert await store.list_events("thread-2", "run-1") == []
    assert await store.list_events("thread-1", "run-2") == []


@pytest.mark.anyio
async def test_run_journal_task_event_action_result_and_artifact_refs_follow_parent_task_run():
    store = MemoryRunEventStore()
    journal = RunJournal(run_id="run-1", thread_id="thread-1", event_store=store, user_id="user-1")
    event = {
        "type": "task.completed",
        "thread_id": "thread-1",
        "run_id": "run-1",
        "task_id": "task-1",
        "action_result": {"status": "success", "summary": "done"},
        "artifact_refs": [{"artifact_id": "artifact-1", "name": "result.md"}],
    }

    journal.record_task_event(event)
    await journal.flush()

    records = await store.list_events("thread-1", "run-1", event_types=["task.completed"], user_id="user-1")
    assert len(records) == 1
    record = records[0]
    assert record["thread_id"] == "thread-1"
    assert record["run_id"] == "run-1"
    assert record["category"] == "message"
    assert record["metadata"] == {
        "caller": "task_event",
        "thread_id": "thread-1",
        "run_id": "run-1",
        "task_id": "task-1",
    }
    assert record["content"]["action_result"] == {"status": "success", "summary": "done"}
    assert record["content"]["artifact_refs"] == [{"artifact_id": "artifact-1", "name": "result.md"}]
    assert [m["seq"] for m in await store.list_messages_by_run("thread-1", "run-1", user_id="user-1")] == [record["seq"]]
