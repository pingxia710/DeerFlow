"""Focused regression tests for durable SSE task-event replay."""

import json
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_sse_durable_replay_task_failed_order_and_single_terminal(monkeypatch):
    import app.gateway.services as gateway_services
    from deerflow.runtime import DisconnectMode, RunRecord, RunStatus
    from deerflow.runtime.events.store.memory import MemoryRunEventStore

    thread_id = "thread-durable-order"
    run_id = "run-durable-order"
    user_id = "user-durable-order"
    store = MemoryRunEventStore()
    monkeypatch.setattr(gateway_services, "_TASK_EVENT_REPLAY_PAGE_SIZE", 2)

    for event_type, task_id in [
        ("task_started", "task-a"),
        ("task_failed", "task-a"),
        ("task_started", "task-b"),
    ]:
        await store.put(
            thread_id=thread_id,
            run_id=run_id,
            event_type=event_type,
            category="message",
            content={
                "schema_version": "deerflow.task-event/v1",
                "type": event_type,
                "event_type": event_type,
                "thread_id": thread_id,
                "run_id": run_id,
                "task_id": task_id,
                "status": "failed" if event_type == "task_failed" else "running",
            },
            metadata={"caller": "task_event"},
            user_id=user_id,
        )
    await store.put(thread_id=thread_id, run_id=run_id, event_type="run.terminal", category="lifecycle", content={"status": "error", "terminal_reason": "failed"}, metadata={"caller": "runtime"}, user_id=user_id)

    class CleanedBridge:
        async def subscribe(self, *_args, **_kwargs):
            raise AssertionError("must replay from durable store only")
            yield

    record = RunRecord(run_id=run_id, thread_id=thread_id, assistant_id="lead_agent", status=RunStatus.error, terminal_reason="failed", on_disconnect=DisconnectMode.continue_, user_id=user_id)
    frames = []
    async for frame in gateway_services.sse_consumer(CleanedBridge(), record, SimpleNamespace(headers={}, is_disconnected=lambda: False), SimpleNamespace(cancel=lambda _run_id: None), event_store=store, user_id=user_id):
        frames.append(frame)

    payloads = [json.loads(frame.split("data: ", 1)[1].split("\n", 1)[0]) for frame in frames if frame.startswith("event: custom")]
    assert [(payload["event_type"], payload.get("task_id")) for payload in payloads] == [("task_started", "task-a"), ("task_failed", "task-a"), ("task_started", "task-b"), ("run.terminal", None)]
    assert sum(1 for payload in payloads if payload["event_type"] == "run.terminal") == 1
    assert frames[-1].startswith("event: end")
