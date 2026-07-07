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
            "task_id": "wrong-thread",
            "thread_id": "thread-2",
            "run_id": "run-1",
        }
    )
    journal.record_task_event(
        {
            "type": "task_running",
            "task_id": "wrong-run",
            "thread_id": "thread-1",
            "run_id": "run-2",
        }
    )
    journal.record_task_event(
        {
            "type": "task_running",
            "task_id": "matching",
            "thread_id": "thread-1",
            "run_id": "run-1",
        }
    )
    await journal.flush()

    rows = await store.list_messages_by_run("thread-1", "run-1")
    assert len(rows) == 1
    assert rows[0]["metadata"]["task_id"] == "matching"


@pytest.mark.asyncio
async def test_task_event_terminal_identity_sequence_is_not_mixed_between_tasks():
    store = MemoryRunEventStore()
    journal = RunJournal("run-1", "thread-1", store, flush_threshold=100)

    journal.record_task_event({"type": "task_running", "task_id": "task-a", "thread_id": "thread-1", "run_id": "run-1"})
    journal.record_task_event({"type": "task_completed", "task_id": "task-a", "thread_id": "thread-1", "run_id": "run-1", "result": "done"})
    journal.record_task_event({"type": "task_failed", "task_id": "task-b", "thread_id": "thread-1", "run_id": "run-1", "error": "boom"})
    await journal.flush()

    rows = await store.list_messages_by_run("thread-1", "run-1")
    assert [row["event_type"] for row in rows] == ["task_running", "task_completed", "task_failed"]
    assert [row["metadata"]["task_id"] for row in rows] == ["task-a", "task-a", "task-b"]
    assert all(row["metadata"]["thread_id"] == "thread-1" and row["metadata"]["run_id"] == "run-1" for row in rows)
    assert rows[1]["content"]["task_id"] == "task-a"
    assert rows[1]["content"]["run_id"] == "run-1"
    assert rows[2]["content"]["task_id"] == "task-b"
    assert rows[2]["content"]["error"] == "boom"


@pytest.mark.asyncio
async def test_task_event_replay_is_scoped_to_each_thread_run_under_mock_pressure():
    store = MemoryRunEventStore()
    journal_a = RunJournal("run-a", "thread-a", store, flush_threshold=100)
    journal_b = RunJournal("run-b", "thread-b", store, flush_threshold=100)

    for index in range(6):
        journal_a.record_task_event({"type": "task_running", "task_id": f"a-{index}", "thread_id": "thread-a", "run_id": "run-a"})
        journal_b.record_task_event({"type": "task_running", "task_id": f"b-{index}", "thread_id": "thread-b", "run_id": "run-b"})

    await journal_a.flush()
    await journal_b.flush()

    rows_a = await store.list_messages_by_run("thread-a", "run-a")
    rows_b = await store.list_messages_by_run("thread-b", "run-b")

    assert [row["metadata"]["task_id"] for row in rows_a] == [f"a-{index}" for index in range(6)]
    assert [row["metadata"]["task_id"] for row in rows_b] == [f"b-{index}" for index in range(6)]
    assert all(row["metadata"]["thread_id"] == "thread-a" and row["metadata"]["run_id"] == "run-a" for row in rows_a)
    assert all(row["metadata"]["thread_id"] == "thread-b" and row["metadata"]["run_id"] == "run-b" for row in rows_b)
    assert await store.list_messages_by_run("thread-a", "run-b") == []
    assert await store.list_messages_by_run("thread-b", "run-a") == []


@pytest.mark.asyncio
async def test_p2a_fake_multi_session_rehearsal_keeps_journal_replay_isolated():
    """P2-a: deterministic local fake command-room pressure without services/providers."""
    store = MemoryRunEventStore()
    journals = {}

    rooms = [f"room-{room_index}" for room_index in range(5)]
    for room in rooms:
        for conversation_index in range(2):
            thread_id = f"command-room-{room}-conversation-{conversation_index}"
            for round_index in range(2):
                run_id = f"run-round-{round_index}"
                journals[(thread_id, run_id)] = RunJournal(run_id, thread_id, store, flush_threshold=1000)

    expected_counts = {key: 0 for key in journals}
    expected_terminal_counts = {key: {"task_completed": 0, "task_failed": 0} for key in journals}

    # Interleave writes by subtask slot first, so events for different rooms,
    # conversations, and runs are mixed in the shared MemoryRunEventStore.
    for subtask_index in range(6):
        for room in rooms:
            for conversation_index in range(2):
                thread_id = f"command-room-{room}-conversation-{conversation_index}"
                for round_index in range(2):
                    # Alternate 5/6 subtasks per round: round 0 has 5, round 1 has 6.
                    if subtask_index >= 5 + round_index:
                        continue
                    run_id = f"run-round-{round_index}"
                    journal = journals[(thread_id, run_id)]
                    # Deliberately reuse identical task_id values across every
                    # thread/run; isolation must come from thread_id+run_id.
                    task_id = f"shared-task-{subtask_index}"
                    terminal_type = "task_failed" if subtask_index == 0 else "task_completed"
                    events = [
                        {
                            "type": "task_running",
                            "task_id": task_id,
                            "thread_id": thread_id,
                            "run_id": run_id,
                            "room_id": room,
                            "conversation_index": conversation_index,
                            "round_index": round_index,
                        },
                        {
                            "type": terminal_type,
                            "task_id": task_id,
                            "thread_id": thread_id,
                            "run_id": run_id,
                            "room_id": room,
                            "conversation_index": conversation_index,
                            "round_index": round_index,
                            "result": "ok" if terminal_type == "task_completed" else None,
                            "error": "fake failure" if terminal_type == "task_failed" else None,
                        },
                    ]
                    for event in events:
                        journal.record_task_event(event)
                        expected_counts[(thread_id, run_id)] += 1
                    expected_terminal_counts[(thread_id, run_id)][terminal_type] += 1

    for journal in journals.values():
        await journal.flush()

    task_event_types = ["task_running", "task_completed", "task_failed"]
    for (thread_id, run_id), expected_count in expected_counts.items():
        rows = await store.list_messages_by_run(thread_id, run_id, limit=1000)
        replay_rows = await store.list_events(thread_id, run_id, event_types=task_event_types, limit=1000)

        assert len(rows) == expected_count
        assert rows == replay_rows
        assert all(row["category"] == "message" for row in rows)
        assert all(row["metadata"]["thread_id"] == thread_id for row in rows)
        assert all(row["metadata"]["run_id"] == run_id for row in rows)
        assert all(row["content"]["thread_id"] == thread_id for row in rows)
        assert all(row["content"]["run_id"] == run_id for row in rows)
        assert {row["metadata"]["task_id"] for row in rows} == {f"shared-task-{index}" for index in range(expected_count // 2)}

        terminal_rows = [row for row in rows if row["event_type"] in {"task_completed", "task_failed"}]
        assert len(terminal_rows) == expected_count // 2
        assert sum(1 for row in terminal_rows if row["event_type"] == "task_completed") == expected_terminal_counts[(thread_id, run_id)]["task_completed"]
        assert sum(1 for row in terminal_rows if row["event_type"] == "task_failed") == expected_terminal_counts[(thread_id, run_id)]["task_failed"]
        assert all(row["metadata"]["task_id"] == row["content"]["task_id"] for row in terminal_rows)

    for room in rooms:
        for conversation_index in range(2):
            thread_id = f"command-room-{room}-conversation-{conversation_index}"
            assert await store.list_messages_by_run(thread_id, "missing-run") == []
            assert await store.list_events(thread_id, "missing-run", event_types=task_event_types) == []

    assert await store.list_messages_by_run("missing-thread", "run-round-0") == []
    assert await store.list_events("missing-thread", "run-round-0", event_types=task_event_types) == []
