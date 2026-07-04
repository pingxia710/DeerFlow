from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers import runs, thread_runs
from deerflow.runtime import DisconnectMode, RunRecord, RunStatus


@pytest.mark.parametrize(
    ("router_module", "router", "path"),
    [
        (runs, runs.router, "/api/runs/stream"),
        (thread_runs, thread_runs.router, "/api/threads/thread-1/runs/stream"),
    ],
)
def test_stream_create_routes_pass_replay_context(monkeypatch, router_module, router, path):
    user = User(email="stream-context@example.com", password_hash="x", system_role="user", id=uuid4())
    event_store = MagicMock(name="event_store")
    captured: dict[str, object | None] = {}

    async def fake_start_run(body, thread_id, request):
        return RunRecord(
            run_id="run-1",
            thread_id=thread_id,
            assistant_id=None,
            status=RunStatus.running,
            on_disconnect=DisconnectMode.continue_,
            user_id=str(user.id),
        )

    async def fake_sse_consumer(bridge, record, request, run_manager, **kwargs):
        captured["event_store"] = kwargs.get("event_store")
        captured["user_id"] = kwargs.get("user_id")
        yield "event: end\ndata: {}\n\n"

    monkeypatch.setattr(router_module, "start_run", fake_start_run)
    monkeypatch.setattr(router_module, "sse_consumer", fake_sse_consumer)

    app = make_authed_test_app(user_factory=lambda: user)
    app.include_router(router)
    app.state.stream_bridge = MagicMock(name="stream_bridge")
    app.state.run_manager = MagicMock(name="run_manager")
    app.state.run_event_store = event_store

    with TestClient(app) as client:
        response = client.post(path, json={})

    assert response.status_code == 200
    assert captured == {"event_store": event_store, "user_id": str(user.id)}


@pytest.mark.parametrize(
    "path",
    [
        "/api/threads/thread-1/runs/run-1/join",
        "/api/threads/thread-1/runs/run-1/stream",
    ],
)
def test_existing_thread_stream_routes_pass_replay_context(monkeypatch, path):
    user = User(email="join-context@example.com", password_hash="x", system_role="user", id=uuid4())
    event_store = MagicMock(name="event_store")
    captured: dict[str, object | None] = {}

    async def fake_sse_consumer(bridge, record, request, run_manager, **kwargs):
        captured["event_store"] = kwargs.get("event_store")
        captured["user_id"] = kwargs.get("user_id")
        yield "event: end\ndata: {}\n\n"

    monkeypatch.setattr(thread_runs, "sse_consumer", fake_sse_consumer)

    app = make_authed_test_app(user_factory=lambda: user)
    app.include_router(thread_runs.router)
    app.state.stream_bridge = MagicMock(name="stream_bridge")
    app.state.run_event_store = event_store
    app.state.run_manager = MagicMock(name="run_manager")
    app.state.run_manager.get = AsyncMock(
        return_value=RunRecord(
            run_id="run-1",
            thread_id="thread-1",
            assistant_id=None,
            status=RunStatus.running,
            on_disconnect=DisconnectMode.continue_,
            user_id=str(user.id),
        )
    )

    with TestClient(app) as client:
        response = client.get(path)

    assert response.status_code == 200
    assert captured == {"event_store": event_store, "user_id": str(user.id)}
