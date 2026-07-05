"""Tests for SSE frame formatting utilities."""

import asyncio
import json
from uuid import uuid4

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from app.gateway.auth.models import User
from app.gateway.routers import thread_runs
from deerflow.runtime import DisconnectMode, MemoryStreamBridge, RunContext, RunManager, RunRecord, RunStatus, run_agent
from deerflow.runtime.events.store.memory import MemoryRunEventStore


def _format_sse(event: str, data, *, event_id: str | None = None) -> str:
    from app.gateway.services import format_sse

    return format_sse(event, data, event_id=event_id)


def test_sse_end_event_data_null():
    """End event should have data: null."""
    frame = _format_sse("end", None)
    assert "data: null" in frame


def test_sse_metadata_event():
    """Metadata event should include run_id and attempt."""
    frame = _format_sse("metadata", {"run_id": "abc", "attempt": 1}, event_id="123-0")
    assert "event: metadata" in frame
    assert "id: 123-0" in frame


def test_sse_error_format():
    """Error event should use message/name format."""
    frame = _format_sse("error", {"message": "boom", "name": "ValueError"})
    parsed = json.loads(frame.split("data: ")[1].split("\n")[0])
    assert parsed["message"] == "boom"
    assert parsed["name"] == "ValueError"


class _NeverDisconnectedRequest:
    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = headers or {}

    async def is_disconnected(self) -> bool:
        return False


class _CancelRecorder:
    def __init__(self) -> None:
        self.cancelled: list[str] = []

    async def cancel(self, run_id: str) -> None:
        self.cancelled.append(run_id)


class _FinalAnswerAgent:
    metadata: dict = {}
    checkpointer = None
    store = None
    interrupt_before_nodes: list[str] = []
    interrupt_after_nodes: list[str] = []

    async def astream(self, _graph_input, *, config, stream_mode):
        message = AIMessage(content="durable final answer", id="ai-final")
        response = LLMResult(generations=[[ChatGeneration(message=message)]])
        for callback in config.get("callbacks", []):
            callback.on_llm_end(response, run_id=uuid4(), parent_run_id=None, tags=["lead_agent"])
        yield {"messages": [message]}


def _parse_sse_frame(frame: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in frame.splitlines():
        if not line or line.startswith(":"):
            continue
        name, value = line.split(": ", 1)
        fields[name] = value
    return fields


@pytest.mark.asyncio
async def test_sse_consumer_forwards_task_custom_event_without_extra_data_wrapper():
    """Task custom events must reach frontend onCustomEvent as the raw payload."""
    from app.gateway.services import sse_consumer

    bridge = MemoryStreamBridge()
    run_id = "run-task-sse"
    task_event = {
        "schema_version": "deerflow.task-event/v1",
        "type": "task_started",
        "event_type": "task_started",
        "thread_id": "thread-1",
        "run_id": run_id,
        "task_id": "task-1",
        "status": "running",
        "redacted": True,
        "started_at": "2026-07-04T00:00:00Z",
        "completed_at": None,
        "duration_ms": None,
        "result_preview": None,
        "error_preview": None,
        "artifact_refs": [],
        "action_result": None,
        "usage": None,
    }
    await bridge.publish(run_id, "custom", task_event)
    await bridge.publish_end(run_id)

    record = RunRecord(
        run_id=run_id,
        thread_id="thread-1",
        assistant_id="lead_agent",
        status=RunStatus.running,
        on_disconnect=DisconnectMode.continue_,
    )

    frames = []
    async for frame in sse_consumer(bridge, record, _NeverDisconnectedRequest(), _CancelRecorder()):
        frames.append(frame)

    assert len(frames) == 2
    fields = _parse_sse_frame(frames[0])
    assert fields["event"] == "custom"
    assert fields["id"]

    payload = json.loads(fields["data"])
    assert payload == task_event
    assert payload["schema_version"] == "deerflow.task-event/v1"
    assert payload["type"] == "task_started"
    assert payload["event_type"] == "task_started"
    assert payload["run_id"] == run_id
    assert payload["task_id"] == "task-1"
    assert payload["status"] == "running"
    assert "data" not in payload
    assert "event" not in payload

    end_fields = _parse_sse_frame(frames[1])
    assert end_fields["event"] == "end"
    assert json.loads(end_fields["data"]) is None


@pytest.mark.asyncio
async def test_sse_consumer_replays_persisted_task_events_when_bridge_requires_recovery():
    from app.gateway.services import sse_consumer

    thread_id = "thread-replay"
    run_id = "run-replay"
    user_id = "user-replay"
    bridge = MemoryStreamBridge(queue_maxsize=1)
    event_store = MemoryRunEventStore()
    task_event = {
        "schema_version": "deerflow.task-event/v1",
        "type": "task_started",
        "event_type": "task_started",
        "thread_id": thread_id,
        "run_id": run_id,
        "task_id": "task-1",
        "status": "running",
    }
    await event_store.put(
        thread_id=thread_id,
        run_id=run_id,
        event_type="task_started",
        category="message",
        content=task_event,
        metadata={"caller": "task_event"},
        user_id=user_id,
    )
    await event_store.put(
        thread_id=thread_id,
        run_id=run_id,
        event_type="llm.ai.response",
        category="message",
        content={"type": "ai", "content": "durable reply", "id": "ai-1"},
        metadata={"caller": "lead_agent"},
        user_id=user_id,
    )

    await bridge.publish(run_id, "values", {"old": True})
    last_event_id = bridge._streams[run_id].events[0].id
    await bridge.publish(run_id, "values", {"missed": True})
    await bridge.publish(run_id, "values", {"live": True})
    await bridge.publish_end(run_id)

    record = RunRecord(
        run_id=run_id,
        thread_id=thread_id,
        assistant_id="lead_agent",
        status=RunStatus.running,
        on_disconnect=DisconnectMode.continue_,
        user_id=user_id,
    )
    request = _NeverDisconnectedRequest(headers={"Last-Event-ID": last_event_id})
    frames = []
    async for frame in sse_consumer(
        bridge,
        record,
        request,
        _CancelRecorder(),
        event_store=event_store,
        user_id=user_id,
    ):
        frames.append(frame)

    events = [_parse_sse_frame(frame)["event"] for frame in frames]
    assert events == ["custom", "messages", "stream_recovery_required", "values", "end"]
    assert json.loads(_parse_sse_frame(frames[0])["data"]) == task_event
    assert json.loads(_parse_sse_frame(frames[1])["data"]) == [{"type": "ai", "content": "durable reply", "id": "ai-1"}, {"caller": "lead_agent"}]
    assert json.loads(_parse_sse_frame(frames[3])["data"]) == {"live": True}


@pytest.mark.asyncio
async def test_sse_consumer_replays_persisted_task_events_across_pages(monkeypatch):
    import app.gateway.services as gateway_services

    thread_id = "thread-replay-pages"
    run_id = "run-replay-pages"
    user_id = "user-replay-pages"
    bridge = MemoryStreamBridge(queue_maxsize=1)
    event_store = MemoryRunEventStore()
    monkeypatch.setattr(gateway_services, "_TASK_EVENT_REPLAY_PAGE_SIZE", 2)
    for index in range(3):
        await event_store.put(
            thread_id=thread_id,
            run_id=run_id,
            event_type="task_started",
            category="message",
            content={
                "schema_version": "deerflow.task-event/v1",
                "type": "task_started",
                "event_type": "task_started",
                "thread_id": thread_id,
                "run_id": run_id,
                "task_id": f"task-{index}",
                "status": "running",
            },
            metadata={"caller": "task_event"},
            user_id=user_id,
        )

    await bridge.publish(run_id, "values", {"old": True})
    last_event_id = bridge._streams[run_id].events[0].id
    await bridge.publish(run_id, "values", {"missed": True})
    await bridge.publish(run_id, "values", {"live": True})
    await bridge.publish_end(run_id)

    record = RunRecord(
        run_id=run_id,
        thread_id=thread_id,
        assistant_id="lead_agent",
        status=RunStatus.running,
        on_disconnect=DisconnectMode.continue_,
        user_id=user_id,
    )
    frames = []
    async for frame in gateway_services.sse_consumer(
        bridge,
        record,
        _NeverDisconnectedRequest(headers={"Last-Event-ID": last_event_id}),
        _CancelRecorder(),
        event_store=event_store,
        user_id=user_id,
    ):
        frames.append(frame)

    task_ids = [json.loads(_parse_sse_frame(frame)["data"])["task_id"] for frame in frames if _parse_sse_frame(frame)["event"] == "custom"]
    assert task_ids == ["task-0", "task-1", "task-2"]


@pytest.mark.asyncio
async def test_sse_consumer_replays_only_current_owner_task_events():
    from app.gateway.services import sse_consumer

    thread_id = "thread-owner-replay"
    run_id = "run-owner-replay"
    user_id = "user-a"
    bridge = MemoryStreamBridge(queue_maxsize=1)
    event_store = MemoryRunEventStore()
    for owner, task_id in [("user-a", "task-a"), ("user-b", "task-b")]:
        await event_store.put(
            thread_id=thread_id,
            run_id=run_id,
            event_type="task_started",
            category="message",
            content={
                "schema_version": "deerflow.task-event/v1",
                "type": "task_started",
                "event_type": "task_started",
                "thread_id": thread_id,
                "run_id": run_id,
                "task_id": task_id,
                "status": "running",
            },
            metadata={"caller": "task_event"},
            user_id=owner,
        )

    await bridge.publish(run_id, "values", {"old": True})
    last_event_id = bridge._streams[run_id].events[0].id
    await bridge.publish(run_id, "values", {"missed": True})
    await bridge.publish(run_id, "values", {"live": True})
    await bridge.publish_end(run_id)

    record = RunRecord(
        run_id=run_id,
        thread_id=thread_id,
        assistant_id="lead_agent",
        status=RunStatus.running,
        on_disconnect=DisconnectMode.continue_,
        user_id=user_id,
    )
    frames = []
    async for frame in sse_consumer(
        bridge,
        record,
        _NeverDisconnectedRequest(headers={"Last-Event-ID": last_event_id}),
        _CancelRecorder(),
        event_store=event_store,
        user_id=user_id,
    ):
        frames.append(frame)

    payloads = [json.loads(_parse_sse_frame(frame)["data"]) for frame in frames if _parse_sse_frame(frame)["event"] == "custom"]
    assert [payload["task_id"] for payload in payloads] == ["task-a"]


@pytest.mark.asyncio
async def test_sse_consumer_replays_terminal_task_events_without_bridge_subscription():
    from app.gateway.services import sse_consumer

    thread_id = "thread-terminal-replay"
    run_id = "run-terminal-replay"
    user_id = "user-terminal"
    event_store = MemoryRunEventStore()
    await event_store.put(
        thread_id=thread_id,
        run_id=run_id,
        event_type="task_started",
        category="message",
        content={
            "schema_version": "deerflow.task-event/v1",
            "type": "task_started",
            "event_type": "task_started",
            "thread_id": thread_id,
            "run_id": run_id,
            "task_id": "task-terminal",
            "status": "running",
        },
        metadata={"caller": "task_event"},
        user_id=user_id,
    )
    await event_store.put(
        thread_id=thread_id,
        run_id=run_id,
        event_type="task_started",
        category="message",
        content={
            "schema_version": "deerflow.task-event/v1",
            "type": "task_started",
            "event_type": "task_started",
            "thread_id": thread_id,
            "run_id": run_id,
            "task_id": "task-foreign-owner",
            "status": "running",
        },
        metadata={"caller": "task_event"},
        user_id="foreign-owner",
    )
    await event_store.put(
        thread_id=thread_id,
        run_id=run_id,
        event_type="run.terminal",
        category="lifecycle",
        content={"status": "success", "terminal_reason": "success"},
        metadata={"caller": "runtime"},
        user_id=user_id,
    )
    await event_store.put(
        thread_id=thread_id,
        run_id=run_id,
        event_type="run.terminal",
        category="lifecycle",
        content={"status": "error", "terminal_reason": "failed"},
        metadata={"caller": "runtime"},
        user_id="foreign-owner",
    )

    class _CleanedBridge:
        async def subscribe(self, *_args, **_kwargs):
            raise AssertionError("terminal replay should not subscribe to a cleaned stream")
            yield

    record = RunRecord(
        run_id=run_id,
        thread_id=thread_id,
        assistant_id="lead_agent",
        status=RunStatus.success,
        on_disconnect=DisconnectMode.continue_,
        user_id=user_id,
    )
    frames = []
    async for frame in sse_consumer(
        _CleanedBridge(),
        record,
        _NeverDisconnectedRequest(),
        _CancelRecorder(),
        event_store=event_store,
        user_id=user_id,
    ):
        frames.append(frame)

    events = [_parse_sse_frame(frame)["event"] for frame in frames]
    assert events == ["custom", "custom", "end"]
    assert json.loads(_parse_sse_frame(frames[0])["data"])["task_id"] == "task-terminal"
    terminal_payload = json.loads(_parse_sse_frame(frames[1])["data"])
    assert terminal_payload == {
        "type": "run.terminal",
        "event_type": "run.terminal",
        "thread_id": thread_id,
        "run_id": run_id,
        "status": "success",
        "terminal_reason": "success",
    }


@pytest.mark.asyncio
async def test_sse_consumer_synthesizes_terminal_event_from_run_row_without_replay_event():
    from app.gateway.services import sse_consumer

    thread_id = "thread-terminal-row-only"
    run_id = "run-terminal-row-only"

    class _CleanedBridge:
        async def subscribe(self, *_args, **_kwargs):
            raise AssertionError("terminal row replay should not subscribe to a cleaned stream")
            yield

    record = RunRecord(
        run_id=run_id,
        thread_id=thread_id,
        assistant_id="lead_agent",
        status=RunStatus.error,
        terminal_reason="worker_lost",
        on_disconnect=DisconnectMode.continue_,
        user_id="user-terminal-row-only",
    )
    frames = []
    async for frame in sse_consumer(
        _CleanedBridge(),
        record,
        _NeverDisconnectedRequest(),
        _CancelRecorder(),
        event_store=MemoryRunEventStore(),
        user_id=record.user_id,
    ):
        frames.append(frame)

    events = [_parse_sse_frame(frame)["event"] for frame in frames]
    assert events == ["custom", "end"]
    assert json.loads(_parse_sse_frame(frames[0])["data"]) == {
        "type": "run.terminal",
        "event_type": "run.terminal",
        "thread_id": thread_id,
        "run_id": run_id,
        "status": "error",
        "terminal_reason": "worker_lost",
    }


@pytest.mark.asyncio
async def test_sse_consumer_synthesizes_live_terminal_before_end_when_custom_event_is_missing():
    from app.gateway.services import sse_consumer
    from deerflow.runtime.stream_bridge.base import END_SENTINEL

    thread_id = "thread-live-terminal-row-only"
    run_id = "run-live-terminal-row-only"

    record = RunRecord(
        run_id=run_id,
        thread_id=thread_id,
        assistant_id="lead_agent",
        status=RunStatus.running,
        on_disconnect=DisconnectMode.continue_,
        user_id="user-live-terminal-row-only",
    )

    class _EndOnlyBridge:
        async def subscribe(self, *_args, **_kwargs):
            record.status = RunStatus.success
            yield END_SENTINEL

    frames = []
    async for frame in sse_consumer(_EndOnlyBridge(), record, _NeverDisconnectedRequest(), _CancelRecorder()):
        frames.append(frame)

    events = [_parse_sse_frame(frame)["event"] for frame in frames]
    assert events == ["custom", "end"]
    assert json.loads(_parse_sse_frame(frames[0])["data"]) == {
        "type": "run.terminal",
        "event_type": "run.terminal",
        "thread_id": thread_id,
        "run_id": run_id,
        "status": "success",
        "terminal_reason": "success",
    }


@pytest.mark.asyncio
async def test_sse_consumer_synthesizes_normalized_worker_lost_terminal_reason():
    from app.gateway.services import sse_consumer

    thread_id = "thread-terminal-normalized"

    class _CleanedBridge:
        async def subscribe(self, *_args, **_kwargs):
            raise AssertionError("terminal row replay should not subscribe to a cleaned stream")
            yield

    records = [
        RunRecord(
            run_id="run-terminal-lease-recovered",
            thread_id=thread_id,
            assistant_id="lead_agent",
            status=RunStatus.error,
            terminal_reason="lease_expired_recovered",
            on_disconnect=DisconnectMode.continue_,
        ),
        RunRecord(
            run_id="run-terminal-restart-recovered",
            thread_id=thread_id,
            assistant_id="lead_agent",
            status=RunStatus.error,
            error="Gateway restarted before this run reached a durable final state.",
            on_disconnect=DisconnectMode.continue_,
        ),
    ]

    reasons = []
    for record in records:
        frames = []
        async for frame in sse_consumer(
            _CleanedBridge(),
            record,
            _NeverDisconnectedRequest(),
            _CancelRecorder(),
            event_store=MemoryRunEventStore(),
            user_id=None,
        ):
            frames.append(frame)
        reasons.append(json.loads(_parse_sse_frame(frames[0])["data"])["terminal_reason"])

    assert reasons == ["worker_lost", "worker_lost"]


@pytest.mark.asyncio
async def test_sse_consumer_replays_persisted_messages_and_keeps_recovery_signal():
    from app.gateway.services import sse_consumer

    thread_id = "thread-empty-replay"
    run_id = "run-empty-replay"
    user_id = "user-empty-replay"
    bridge = MemoryStreamBridge(queue_maxsize=1)
    event_store = MemoryRunEventStore()
    await event_store.put(
        thread_id=thread_id,
        run_id=run_id,
        event_type="llm.ai.response",
        category="message",
        content={"type": "ai", "content": "replayed message", "id": "ai-replay"},
        metadata={"caller": "lead_agent"},
        user_id=user_id,
    )

    await bridge.publish(run_id, "values", {"old": True})
    last_event_id = bridge._streams[run_id].events[0].id
    await bridge.publish(run_id, "values", {"missed": True})
    await bridge.publish(run_id, "values", {"live": True})
    await bridge.publish_end(run_id)

    record = RunRecord(
        run_id=run_id,
        thread_id=thread_id,
        assistant_id="lead_agent",
        status=RunStatus.running,
        on_disconnect=DisconnectMode.continue_,
        user_id=user_id,
    )
    request = _NeverDisconnectedRequest(headers={"Last-Event-ID": last_event_id})
    frames = []
    async for frame in sse_consumer(
        bridge,
        record,
        request,
        _CancelRecorder(),
        event_store=event_store,
        user_id=user_id,
    ):
        frames.append(frame)

    events = [_parse_sse_frame(frame)["event"] for frame in frames]
    assert events == ["messages", "stream_recovery_required", "values", "end"]
    assert json.loads(_parse_sse_frame(frames[0])["data"]) == [{"type": "ai", "content": "replayed message", "id": "ai-replay"}, {"caller": "lead_agent"}]
    assert json.loads(_parse_sse_frame(frames[2])["data"]) == {"live": True}


@pytest.mark.asyncio
async def test_sse_consumer_prefers_persisted_stream_frames_for_durable_replay():
    from app.gateway.services import sse_consumer

    thread_id = "thread-stream-projection"
    run_id = "run-stream-projection"
    user_id = "user-stream-projection"
    bridge = MemoryStreamBridge(queue_maxsize=1)
    event_store = MemoryRunEventStore()
    await event_store.put(
        thread_id=thread_id,
        run_id=run_id,
        event_type="stream.metadata",
        category="stream",
        content={"event": "metadata", "data": {"run_id": run_id, "thread_id": thread_id}},
        user_id=user_id,
    )
    await event_store.put(
        thread_id=thread_id,
        run_id=run_id,
        event_type="llm.ai.response",
        category="message",
        content={"type": "ai", "content": "fallback duplicate", "id": "ai-fallback"},
        user_id=user_id,
    )
    await event_store.put(
        thread_id=thread_id,
        run_id=run_id,
        event_type="stream.messages",
        category="stream",
        content={"event": "messages", "data": [{"type": "ai", "content": "stream token", "id": "ai-stream"}, {"langgraph_node": "agent"}]},
        user_id=user_id,
    )
    await event_store.put(
        thread_id=thread_id,
        run_id=run_id,
        event_type="stream.values",
        category="stream",
        content={"event": "values", "data": {"messages": [{"type": "ai", "content": "full state", "id": "ai-stream"}]}},
        user_id=user_id,
    )

    await bridge.publish(run_id, "values", {"old": True})
    last_event_id = bridge._streams[run_id].events[0].id
    await bridge.publish(run_id, "values", {"missed": True})
    await bridge.publish(run_id, "values", {"live": True})
    await bridge.publish_end(run_id)

    record = RunRecord(
        run_id=run_id,
        thread_id=thread_id,
        assistant_id="lead_agent",
        status=RunStatus.running,
        on_disconnect=DisconnectMode.continue_,
        user_id=user_id,
    )
    frames = []
    async for frame in sse_consumer(
        bridge,
        record,
        _NeverDisconnectedRequest(headers={"Last-Event-ID": last_event_id}),
        _CancelRecorder(),
        event_store=event_store,
        user_id=user_id,
    ):
        frames.append(frame)

    events = [_parse_sse_frame(frame)["event"] for frame in frames]
    assert events[:4] == ["metadata", "messages", "values", "stream_recovery_required"]
    assert "fallback duplicate" not in "\n".join(frames)
    assert json.loads(_parse_sse_frame(frames[1])["data"])[0]["content"] == "stream token"
    assert json.loads(_parse_sse_frame(frames[2])["data"])["messages"][0]["content"] == "full state"


@pytest.mark.asyncio
async def test_sse_end_waits_until_run_messages_are_durable():
    from app.gateway.services import sse_consumer

    thread_id = "thread-durable"
    event_store = MemoryRunEventStore()
    run_manager = RunManager()
    bridge = MemoryStreamBridge()
    user_id = uuid4()
    record = await run_manager.create(
        thread_id,
        assistant_id="lead_agent",
        on_disconnect=DisconnectMode.continue_,
        user_id=str(user_id),
    )
    app = make_authed_test_app(user_factory=lambda: User(id=user_id, email="durable@example.com", password_hash="x", system_role="user"))
    app.include_router(thread_runs.router)
    app.state.run_event_store = event_store
    app.state.run_manager = run_manager

    def factory(*, config):
        return _FinalAnswerAgent()

    run_task = asyncio.create_task(
        run_agent(
            bridge,
            run_manager,
            record,
            ctx=RunContext(checkpointer=None, event_store=event_store),
            agent_factory=factory,
            graph_input={},
            config={},
        )
    )

    try:
        frames = []
        async with asyncio.timeout(5):
            async for frame in sse_consumer(bridge, record, _NeverDisconnectedRequest(), run_manager):
                frames.append(frame)

        assert _parse_sse_frame(frames[-1])["event"] == "end"

        with TestClient(app) as client:
            response = client.get(f"/api/threads/{thread_id}/runs/{record.run_id}/messages")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data[-1]["event_type"] == "llm.ai.response"
        assert data[-1]["content"]["type"] == "ai"
        assert data[-1]["content"]["content"] == "durable final answer"

        stream_events = await event_store.list_events(
            thread_id,
            record.run_id,
            event_types=["stream.metadata", "stream.values"],
            user_id=str(user_id),
        )
        assert [row["event_type"] for row in stream_events] == ["stream.metadata", "stream.values"]
        assert stream_events[0]["content"]["data"]["run_id"] == record.run_id
        assert stream_events[1]["content"]["data"]["messages"][0]["content"] == "durable final answer"
    finally:
        await run_task


def test_format_sse_custom_task_event_data_shape_matches_frontend_contract():
    """Formatting alone must not nest task event payload under data/event."""
    task_event = {
        "schema_version": "deerflow.task-event/v1",
        "type": "task_completed",
        "event_type": "task_completed",
        "thread_id": "thread-1",
        "run_id": "run-1",
        "task_id": "task-1",
        "status": "completed",
    }

    frame = _format_sse("custom", task_event, event_id="42-0")
    fields = _parse_sse_frame(frame)

    assert fields["event"] == "custom"
    assert fields["id"] == "42-0"
    payload = json.loads(fields["data"])
    assert payload == task_event
    assert payload["event_type"] == payload["type"] == "task_completed"
    assert "data" not in payload
    assert "event" not in payload
