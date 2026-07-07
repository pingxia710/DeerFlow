"""Focused regression tests for pending timeout, queue-full event surfacing, and SSE durable replay."""

import asyncio
import importlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def test_task_tool_queue_full_surfaces_task_failed_event(monkeypatch):
    task_tool_module = importlib.import_module("deerflow.tools.builtins.task_tool")
    from deerflow.subagents.config import SubagentConfig

    class FakeStatus:
        PENDING = SimpleNamespace(value="pending", is_terminal=False)
        RUNNING = SimpleNamespace(value="running", is_terminal=False)
        COMPLETED = SimpleNamespace(value="completed", is_terminal=True)
        FAILED = SimpleNamespace(value="failed", is_terminal=True)
        CANCELLED = SimpleNamespace(value="cancelled", is_terminal=True)
        TIMED_OUT = SimpleNamespace(value="timed_out", is_terminal=True)

    config = SubagentConfig(name="general-purpose", description="d", system_prompt="p", model="test-model", timeout_seconds=1)
    events = []

    class DummyExecutor:
        def __init__(self, **_kwargs):
            pass

        def execute_async(self, _prompt, task_id=None):
            return task_id or "tc-full"

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeStatus)
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr(
        task_tool_module,
        "get_background_task_result",
        lambda _task_id: SimpleNamespace(
            status=FakeStatus.FAILED,
            error="Subagent queue is full; try again later",
            ai_messages=[],
            token_usage_records=[],
            usage_reported=False,
        ),
    )
    monkeypatch.setattr(task_tool_module, "cleanup_background_task", lambda _task_id: None)
    monkeypatch.setattr(task_tool_module, "record_subagent_handoff", lambda **_kwargs: None)
    monkeypatch.setattr(task_tool_module, "record_pending_handoff", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", lambda _delay: asyncio.sleep(0))
    monkeypatch.setattr("deerflow.tools.get_available_tools", MagicMock(return_value=[]))

    async def run():
        return await task_tool_module.task_tool.coroutine(
            runtime=SimpleNamespace(
                state={},
                context={"thread_id": "thread-q", "run_id": "run-q"},
                config={"metadata": {}},
            ),
            description="queue pressure",
            prompt="work",
            subagent_type="general-purpose",
            tool_call_id="tc-full",
        )

    output = asyncio.run(run())
    assert output == "Task failed. Error: Subagent queue is full; try again later"
    assert [event["type"] for event in events] == ["task_started", "task_failed"]
    assert events[-1]["status"] == "failed"
    assert "queue is full" in events[-1]["error_preview"]
    assert events[-1]["action_result"]["status"] == "failed"


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
