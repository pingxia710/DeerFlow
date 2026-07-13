from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers import thread_runs
from deerflow.command_room.handoff import HandoffEnvelope
from deerflow.command_room.pending_handoff import build_pending_handoff, record_pending_handoff
from deerflow.runtime import RunRecord, RunStatus
from deerflow.runtime.events.store.memory import MemoryRunEventStore

_USER_ID = UUID("44444444-4444-4444-4444-444444444444")


class _Paths:
    def __init__(self, root: Path) -> None:
        self.root = root

    def thread_dir(self, thread_id: str, *, user_id: str | None = None) -> Path:
        return self.root / str(user_id or "legacy") / thread_id


class _ReadOnlyRoundStore:
    def __init__(self, user_id: str) -> None:
        self.rounds = [
            {
                "round_id": "round-1",
                "thread_id": "thread-1",
                "user_id": user_id,
                "current_run_id": "run-1",
                "state": "awaiting_chair_decision",
            }
        ]
        self.task_lanes = [
            {
                "thread_id": "thread-1",
                "run_id": "run-1",
                "task_id": "task-1",
                "round_id": "round-1",
                "user_id": user_id,
                "role": "evidence",
                "status": "running",
            }
        ]
        self.set_run_state_calls: list[dict] = []
        self.record_task_events_calls: list[list[dict]] = []

    async def list_by_thread(self, thread_id: str, *, user_id=None, limit: int = 50):
        return [dict(row) for row in self.rounds if row["thread_id"] == thread_id and row.get("user_id") == user_id][:limit]

    async def list_task_lanes_by_round(self, *, thread_id: str, round_id: str, user_id=None, limit: int = 100):
        return [dict(row) for row in self.task_lanes if row["thread_id"] == thread_id and row["round_id"] == round_id and row.get("user_id") == user_id][:limit]

    async def set_run_state(self, *args, **kwargs):  # pragma: no cover - must not be called by this test
        self.set_run_state_calls.append({"args": args, "kwargs": kwargs})
        raise AssertionError("runtime snapshot close gate facts must not mutate round state")

    async def record_task_events(self, events: list[dict]) -> None:  # pragma: no cover - must not be called by this test
        self.record_task_events_calls.append(events)
        raise AssertionError("runtime snapshot close gate facts must not record task events")


def _user() -> User:
    return User(id=_USER_ID, email=f"{_USER_ID}@example.com", password_hash="x", system_role="user")


def test_runtime_snapshot_includes_read_only_close_gate_facts(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("deerflow.command_room.pending_handoff.get_paths", lambda: _Paths(tmp_path))
    user_id = str(_USER_ID)
    handoff = build_pending_handoff(
        thread_id="thread-1",
        run_id="run-1",
        round_id="round-1",
        task_id="task-1",
        envelope=HandoffEnvelope(
            source_role="worker",
            target_role="Chair",
            task_or_question="Review unresolved evidence before close.",
            evidence_refs=["claim without concrete proof"],
            evidence_strength="Unverified",
            raw_input_sha256="abc123",
        ),
    )
    record_pending_handoff(handoff, user_id=user_id)

    record = RunRecord(
        run_id="run-1",
        thread_id="thread-1",
        assistant_id="command-room",
        status=RunStatus.running,
        on_disconnect="cancel",
        round_id="round-1",
    )
    event_store = MemoryRunEventStore()
    asyncio.run(
        event_store.put_batch(
            [
                {
                    "thread_id": "thread-1",
                    "run_id": "run-1",
                    "event_type": "llm.tool.result",
                    "category": "message",
                    "content": {"name": "other_tool", "content": "weak observation only"},
                    "metadata": {"caller": "lead_agent"},
                    "created_at": "2026-01-01T00:00:01+00:00",
                    "user_id": user_id,
                }
            ]
        )
    )
    round_store = _ReadOnlyRoundStore(user_id)
    app = make_authed_test_app(user_factory=_user)
    app.include_router(thread_runs.router)
    app.state.run_manager = SimpleNamespace(
        list_by_thread=AsyncMock(return_value=[record]),
        recover_stale_inflight_runs=AsyncMock(return_value=[]),
        begin_thread_write=AsyncMock(),
        end_thread_write=AsyncMock(),
    )
    app.state.run_event_store = event_store
    app.state.round_state_store = round_store

    before_rounds = len(round_store.rounds)
    before_lanes = len(round_store.task_lanes)
    before_handoff_status = handoff.status
    with TestClient(app) as client:
        default_response = client.get("/api/threads/thread-1/runtime-snapshot")
        response = client.get("/api/threads/thread-1/runtime-snapshot?include_close_gates=true")

    assert default_response.status_code == 200
    assert default_response.json()["close_gates"] == []
    assert response.status_code == 200
    body = response.json()
    assert "close_gates" in body
    [close_gate] = body["close_gates"]
    assert close_gate["thread_id"] == "thread-1"
    assert close_gate["run_id"] == "run-1"
    assert close_gate["round_id"] == "round-1"
    assert close_gate["programmatic_decision"] is False
    assert close_gate["auto_dispatch"] is False
    assert close_gate["quality_verdict"] is None
    assert close_gate["facts"]
    assert "pending_handoffs=1" in close_gate["facts"]
    assert "task_lanes=1" in close_gate["facts"]
    assert close_gate["unknowns"] == []
    assert close_gate["warnings"] == []
    assert close_gate["open_pending_handoffs"][0]["handoff_id"] == handoff.handoff_id

    assert len(round_store.rounds) == before_rounds
    assert len(round_store.task_lanes) == before_lanes
    assert round_store.set_run_state_calls == []
    assert round_store.record_task_events_calls == []
    assert handoff.status == before_handoff_status
    app.state.run_manager.begin_thread_write.assert_not_awaited()
    app.state.run_manager.end_thread_write.assert_not_awaited()
