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
from deerflow.runtime import DisconnectMode, MemoryStreamBridge, RunContext, RunManager, run_agent
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
    headers: dict[str, str] = {}

    async def is_disconnected(self) -> bool:
        return False


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
    finally:
        await run_task
