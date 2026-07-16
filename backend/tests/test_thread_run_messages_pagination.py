"""Tests for paginated GET /api/threads/{thread_id}/runs/{run_id}/messages endpoint."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from _router_auth_helpers import call_unwrapped, make_authed_test_app
from _run_message_pagination_helpers import assert_run_message_page
from fastapi.testclient import TestClient
from langgraph.store.memory import InMemoryStore
from pydantic import ValidationError

from app.gateway.auth.models import User
from app.gateway.routers import thread_runs
from deerflow.persistence.thread_meta.memory import MemoryThreadMetaStore
from deerflow.runtime import MemoryStreamBridge, RunManager, RunRecord, RunStatus
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.runs.store.memory import MemoryRunStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_TEST_USER_ID = UUID("55555555-5555-5555-5555-555555555555")


def _make_user(user_id: UUID = _TEST_USER_ID) -> User:
    return User(id=user_id, email=f"{user_id}@example.com", password_hash="x", system_role="user")


class _RunManagerForPaginationTests:
    async def get(self, run_id: str, *, user_id=None):
        if not run_id.startswith("run-"):
            return None
        suffix = run_id.removeprefix("run-")
        return RunRecord(
            run_id=run_id,
            thread_id=f"thread-{suffix}",
            assistant_id=None,
            status=RunStatus.success,
            on_disconnect="cancel",
        )


class _RoundStoreForSnapshotTests:
    def __init__(self) -> None:
        self.seen_user_id: str | None = None

    async def list_by_thread(self, thread_id: str, *, user_id=None, limit: int = 50):
        self.seen_user_id = user_id
        return [
            {
                "round_id": "round-1",
                "thread_id": thread_id,
                "user_id": user_id,
                "current_run_id": "run-2",
                "state": "closed",
            }
        ][:limit]

    async def list_task_lanes_by_round(self, *, thread_id: str, round_id: str, user_id=None, limit: int = 100):
        self.seen_user_id = user_id
        return [
            {
                "thread_id": thread_id,
                "run_id": "run-2",
                "task_id": "task-1",
                "round_id": round_id,
                "user_id": user_id,
                "role": "evidence",
                "description": "Collect runtime evidence",
                "result": "Evidence ready",
                "started_at": "2026-01-01T00:01:00+00:00",
                "finished_at": "2026-01-01T00:01:02+00:00",
                "duration_ms": 2000,
                "status": "completed",
            }
        ][:limit]


class _RepairableRoundStoreForSnapshotTests:
    def __init__(
        self,
        *,
        user_id: str,
        rounds: list[dict] | None = None,
        task_lanes: list[dict] | None = None,
    ) -> None:
        self.rounds = rounds or [
            {
                "round_id": "round-stale",
                "thread_id": "thread-1",
                "user_id": user_id,
                "current_run_id": "run-terminal",
                "state": "executing",
            }
        ]
        self.task_lanes = task_lanes or [
            {
                "thread_id": "thread-1",
                "run_id": "run-terminal",
                "task_id": "task-stale",
                "round_id": "round-stale",
                "user_id": user_id,
                "role": "evidence",
                "status": "executing",
            }
        ]
        self.set_run_state_calls: list[dict] = []
        self.record_task_events_calls: list[list[dict]] = []

    async def list_by_thread(self, thread_id: str, *, user_id=None, limit: int = 50):
        return [dict(row) for row in self.rounds if row["thread_id"] == thread_id and row.get("user_id") == user_id][:limit]

    async def list_task_lanes_by_round(self, *, thread_id: str, round_id: str, user_id=None, limit: int = 100):
        return [dict(row) for row in self.task_lanes if row["thread_id"] == thread_id and row["round_id"] == round_id and row.get("user_id") == user_id][:limit]

    async def set_run_state(
        self,
        run_id: str,
        *,
        thread_id: str,
        user_id: str | None,
        round_id: str,
        state: str,
        event_type: str,
        content: dict | None = None,
        next_action: str | None = None,
    ):
        self.set_run_state_calls.append(
            {
                "run_id": run_id,
                "thread_id": thread_id,
                "user_id": user_id,
                "round_id": round_id,
                "state": state,
                "event_type": event_type,
                "content": content,
                "next_action": next_action,
            }
        )
        for row in self.rounds:
            if row["current_run_id"] != run_id:
                continue
            row["state"] = state
            row["closed_at"] = "2026-01-01T00:00:02+00:00"
            return dict(row)
        return None

    async def record_task_events(self, events: list[dict]) -> None:
        self.record_task_events_calls.append(events)
        for event in events:
            for lane in self.task_lanes:
                if lane["run_id"] == event.get("run_id") and lane["task_id"] == event.get("task_id"):
                    lane["status"] = event.get("status") or lane["status"]
                    lane["error"] = event.get("error_preview") or lane.get("error")


def _make_app(event_store=None, run_manager=None, *, user_id: UUID = _TEST_USER_ID):
    """Build a test FastAPI app with stub auth and mocked state."""
    app = make_authed_test_app(user_factory=lambda: _make_user(user_id))
    app.include_router(thread_runs.router)

    if event_store is not None:
        app.state.run_event_store = event_store
    if run_manager is None:
        run_manager = _RunManagerForPaginationTests()
    app.state.run_manager = run_manager

    return app


def _make_event_store(rows: list[dict]):
    """Return an AsyncMock event store whose message listing methods return rows."""
    store = MagicMock()
    store.list_messages_by_run = AsyncMock(return_value=rows)
    store.list_messages = AsyncMock(return_value=rows)
    return store


def _make_message(seq: int) -> dict:
    return {"seq": seq, "event_type": "ai_message", "category": "message", "content": f"msg-{seq}"}


def _make_store_only_run_manager(status: str = "running") -> RunManager:
    store = MemoryRunStore()
    asyncio.run(
        store.put(
            "store-only-run",
            thread_id="thread-store",
            assistant_id="lead_agent",
            status=status,
            multitask_strategy="reject",
            metadata={},
            kwargs={},
            created_at="2026-01-01T00:00:00+00:00",
        )
    )
    return RunManager(store=store)


def test_run_create_request_rejects_unimplemented_enqueue_strategy():
    schema = thread_runs.RunCreateRequest.model_json_schema()

    assert "enqueue" not in json.dumps(schema)
    with pytest.raises(ValidationError):
        thread_runs.RunCreateRequest(multitask_strategy="enqueue")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_returns_paginated_envelope():
    """GET /api/threads/{tid}/runs/{rid}/messages returns {data: [...], has_more: bool}."""
    rows = [_make_message(i) for i in range(1, 4)]
    app = _make_app(event_store=_make_event_store(rows))
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/runs/run-1/messages")
    assert response.status_code == 200
    body = response.json()
    assert "data" in body
    assert "has_more" in body
    assert body["has_more"] is False
    assert len(body["data"]) == 3


@pytest.mark.parametrize("owner_kind", ["missing", "null"])
def test_run_messages_require_explicit_thread_owner(owner_kind):
    event_store = _make_event_store([_make_message(1)])
    app = _make_app(event_store=event_store)
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    if owner_kind == "null":
        asyncio.run(thread_store.create("thread-1", user_id=None))
    app.state.thread_store = thread_store

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/runs/run-1/messages")

    assert response.status_code == 404
    event_store.list_messages_by_run.assert_not_awaited()


def test_runtime_snapshot_returns_runs_messages_rounds_and_task_lanes():
    """GET /api/threads/{tid}/runtime-snapshot returns one recovery envelope."""
    user_id = str(_TEST_USER_ID)
    run_store = MemoryRunStore()
    event_store = MemoryRunEventStore()
    round_store = _RoundStoreForSnapshotTests()
    asyncio.run(
        run_store.put(
            "run-1",
            thread_id="thread-1",
            assistant_id="lead_agent",
            user_id=user_id,
            status="success",
            created_at="2026-01-01T00:00:00+00:00",
        )
    )
    asyncio.run(
        run_store.put(
            "run-2",
            thread_id="thread-1",
            assistant_id="lead_agent",
            user_id=user_id,
            status="success",
            created_at="2026-01-01T00:01:00+00:00",
        )
    )
    asyncio.run(
        run_store.put(
            "run-other-user",
            thread_id="thread-1",
            assistant_id="lead_agent",
            user_id="other-user",
            status="success",
            created_at="2026-01-01T00:02:00+00:00",
        )
    )
    asyncio.run(
        event_store.put_batch(
            [
                {
                    "thread_id": "thread-1",
                    "run_id": "run-1",
                    "event_type": "llm.human.input",
                    "category": "message",
                    "content": {"type": "human", "content": "older question"},
                    "metadata": {"caller": "lead_agent"},
                    "created_at": "2026-01-01T00:00:01+00:00",
                    "user_id": user_id,
                },
                {
                    "thread_id": "thread-1",
                    "run_id": "run-2",
                    "event_type": "llm.ai.response",
                    "category": "message",
                    "content": {"type": "ai", "content": "hidden title"},
                    "metadata": {"caller": "middleware:title"},
                    "created_at": "2026-01-01T00:01:01+00:00",
                    "user_id": user_id,
                },
                {
                    "thread_id": "thread-1",
                    "run_id": "run-other-user",
                    "event_type": "llm.human.input",
                    "category": "message",
                    "content": {"type": "human", "content": "wrong user"},
                    "metadata": {"caller": "lead_agent"},
                    "created_at": "2026-01-01T00:02:01+00:00",
                    "user_id": "other-user",
                },
            ]
        )
    )
    app = _make_app(event_store=event_store, run_manager=RunManager(store=run_store))
    app.state.round_state_store = round_store

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/runtime-snapshot")

    assert response.status_code == 200
    body = response.json()
    assert body["thread_id"] == "thread-1"
    assert [run["run_id"] for run in body["runs"]] == ["run-2", "run-1"]
    assert [page["run_id"] for page in body["run_messages"]] == ["run-2", "run-1"]
    assert body["run_messages"][0]["data"][0]["display"] == {
        "visible_in_chat": False,
        "surface": "control",
        "reason": "middleware_message",
        "message_type": "system_internal_state",
        "payload_types": [],
    }
    assert body["run_messages"][1]["data"][0]["content"]["content"] == "older question"
    assert body["rounds"][0]["round_id"] == "round-1"
    assert body["task_lanes"][0]["task_id"] == "task-1"
    assert body["task_lanes"][0]["subagent_type"] == "evidence"
    assert body["task_lanes"][0]["description"] == "Collect runtime evidence"
    assert body["task_lanes"][0]["result"] == "Evidence ready"
    assert body["task_lanes"][0]["duration_ms"] == 2000
    assert body["task_lanes"][0]["completed_at"] == "2026-01-01T00:01:02+00:00"
    assert body["task_lanes"][0]["prompt"] is None
    assert body.get("recovery") is None
    assert round_store.seen_user_id == user_id


def test_thread_timeline_returns_owner_scoped_cursor_page():
    user_id = str(_TEST_USER_ID)
    event_store = MemoryRunEventStore()
    asyncio.run(
        event_store.put_batch(
            [
                {
                    "thread_id": "thread-1",
                    "run_id": "run-1",
                    "event_type": "llm.human.input",
                    "category": "message",
                    "content": {"type": "human", "content": "first"},
                    "user_id": user_id,
                },
                {
                    "thread_id": "thread-1",
                    "run_id": "run-1",
                    "event_type": "llm.context",
                    "category": "trace",
                    "content": {"hidden": True},
                    "user_id": user_id,
                },
                {
                    "thread_id": "thread-1",
                    "run_id": "run-1",
                    "event_type": "task_started",
                    "category": "message",
                    "content": {"task_id": "task-1", "type": "task_started"},
                    "user_id": user_id,
                },
                {
                    "thread_id": "thread-1",
                    "run_id": "run-1",
                    "event_type": "run.terminal",
                    "category": "lifecycle",
                    "content": {"status": "success", "terminal_reason": "success"},
                    "user_id": user_id,
                },
                {
                    "thread_id": "thread-1",
                    "run_id": "run-other-owner",
                    "event_type": "task_completed",
                    "category": "message",
                    "content": {"task_id": "other", "type": "task_completed"},
                    "user_id": "other-owner",
                },
            ]
        )
    )
    app = _make_app(event_store=event_store)

    with TestClient(app) as client:
        first = client.get("/api/threads/thread-1/timeline?limit=2")

    assert first.status_code == 200
    body = first.json()
    assert body["thread_id"] == "thread-1"
    assert body["after_seq"] == 0
    assert body["watermark_seq"] == 4
    assert [record["seq"] for record in body["records"]] == [3, 4]
    assert [record["event_id"] for record in body["records"]] == ["thread-1:3", "thread-1:4"]
    assert body["truncated"] is True
    assert body["has_more"] is False

    asyncio.run(
        event_store.put(
            thread_id="thread-1",
            run_id="run-1",
            event_type="task_completed",
            category="message",
            content={"task_id": "task-1", "type": "task_completed"},
            user_id=user_id,
        )
    )
    tampered_cursor = f"{body['cursor'][:-1]}{'A' if body['cursor'][-1] != 'A' else 'B'}"
    with TestClient(app) as client:
        next_page = client.get(f"/api/threads/thread-1/timeline?cursor={body['cursor']}")
        invalid = client.get("/api/threads/thread-1/timeline?cursor=not-a-timeline-cursor")
        tampered = client.get(f"/api/threads/thread-1/timeline?cursor={tampered_cursor}")

    assert next_page.status_code == 200
    assert next_page.json()["after_seq"] == 4
    assert [record["seq"] for record in next_page.json()["records"]] == [6]
    assert next_page.json()["watermark_seq"] == 6
    assert invalid.status_code == 409
    assert tampered.status_code == 409


def test_runtime_snapshot_loads_message_pages_concurrently():
    class SlowEventStore:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0

        async def list_messages_by_run(
            self,
            thread_id: str,
            run_id: str,
            *,
            limit: int,
            before_seq: int | None = None,
            after_seq: int | None = None,
            user_id: str | None = None,
        ) -> list[dict]:
            del thread_id, run_id, limit, before_seq, after_seq, user_id
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return []

    store = SlowEventStore()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(run_event_store=store)))
    records = [
        RunRecord(
            run_id=f"run-{index}",
            thread_id="thread-1",
            assistant_id="lead_agent",
            status=RunStatus.success,
            on_disconnect="cancel",
        )
        for index in range(3)
    ]

    pages = asyncio.run(
        thread_runs._list_runtime_snapshot_run_messages(
            records=records,
            thread_id="thread-1",
            request=request,
            user_id="user-1",
            limit=50,
        )
    )

    assert store.max_active > 1
    assert store.max_active <= thread_runs._RUNTIME_SNAPSHOT_MESSAGE_CONCURRENCY
    assert [page.run_id for page in pages] == [record.run_id for record in records]


def test_runtime_snapshot_does_not_repair_terminal_run_with_open_round_and_task_lane():
    """A read snapshot reports stored state without repairing it."""
    user_id = str(_TEST_USER_ID)
    run_store = MemoryRunStore()
    event_store = MemoryRunEventStore()
    round_store = _RepairableRoundStoreForSnapshotTests(user_id=user_id)
    asyncio.run(
        run_store.put(
            "run-terminal",
            thread_id="thread-1",
            assistant_id="lead_agent",
            user_id=user_id,
            status="error",
            metadata={"round_id": "round-stale", "terminal_reason": "worker_lost"},
            error="worker lost",
            created_at="2026-01-01T00:00:00+00:00",
        )
    )
    app = _make_app(event_store=event_store, run_manager=RunManager(store=run_store))
    app.state.round_state_store = round_store

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/runtime-snapshot")

    assert response.status_code == 200
    body = response.json()
    assert body["runs"][0]["status"] == "error"
    assert body["runs"][0]["terminal_reason"] == "worker_lost"
    assert body["rounds"][0]["state"] == "executing"
    assert body["task_lanes"][0]["status"] == "executing"
    assert body.get("recovery") is None
    assert round_store.set_run_state_calls == []
    assert round_store.record_task_events_calls == []


def test_runtime_snapshot_does_not_recover_stale_store_only_inflight_run():
    """Stale-run recovery belongs to startup and run lifecycle boundaries."""
    user_id = str(_TEST_USER_ID)
    run_store = MemoryRunStore()
    asyncio.run(
        run_store.put(
            "stale-run",
            thread_id="thread-1",
            assistant_id="lead_agent",
            user_id=user_id,
            status="running",
            created_at="2026-01-01T00:00:00+00:00",
        )
    )
    run_store._runs["stale-run"]["updated_at"] = "2026-01-01T00:00:00+00:00"
    app = _make_app(event_store=MemoryRunEventStore(), run_manager=RunManager(store=run_store))

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/runtime-snapshot")

    assert response.status_code == 200
    body = response.json()
    assert body["runs"][0]["run_id"] == "stale-run"
    assert body["runs"][0]["status"] == "running"
    assert body["runs"][0]["terminal_reason"] is None
    assert body.get("recovery") is None
    stored = asyncio.run(run_store.get("stale-run", user_id=user_id))
    assert stored["status"] == "running"
    assert stored.get("terminal_reason") is None


def test_runtime_snapshot_keeps_stored_round_and_lane_states_isolated():
    """A read snapshot preserves old and new stored states without writes."""
    user_id = str(_TEST_USER_ID)
    run_store = MemoryRunStore()
    event_store = MemoryRunEventStore()
    asyncio.run(
        run_store.put(
            "run-old",
            thread_id="thread-1",
            assistant_id="lead_agent",
            user_id=user_id,
            status="error",
            metadata={"round_id": "round-old", "terminal_reason": "worker_lost"},
            error="worker lost",
            created_at="2026-01-01T00:00:00+00:00",
        )
    )
    asyncio.run(
        run_store.put(
            "run-new",
            thread_id="thread-1",
            assistant_id="lead_agent",
            user_id=user_id,
            status="running",
            metadata={"round_id": "round-new"},
            created_at="2026-01-01T00:01:00+00:00",
        )
    )
    round_store = _RepairableRoundStoreForSnapshotTests(
        user_id=user_id,
        rounds=[
            {
                "round_id": "round-old",
                "thread_id": "thread-1",
                "user_id": user_id,
                "current_run_id": "run-old",
                "state": "executing",
            },
            {
                "round_id": "round-new",
                "thread_id": "thread-1",
                "user_id": user_id,
                "current_run_id": "run-new",
                "state": "executing",
            },
        ],
        task_lanes=[
            {
                "thread_id": "thread-1",
                "run_id": "run-old",
                "task_id": "task-old",
                "round_id": "round-old",
                "user_id": user_id,
                "role": "evidence",
                "status": "executing",
            },
            {
                "thread_id": "thread-1",
                "run_id": "run-new",
                "task_id": "task-new",
                "round_id": "round-new",
                "user_id": user_id,
                "role": "evidence",
                "status": "in_progress",
            },
        ],
    )
    app = _make_app(event_store=event_store, run_manager=RunManager(store=run_store))
    app.state.round_state_store = round_store

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/runtime-snapshot")

    assert response.status_code == 200
    body = response.json()
    assert [run["run_id"] for run in body["runs"]] == ["run-new", "run-old"]
    round_states = {round_["round_id"]: round_["state"] for round_ in body["rounds"]}
    lane_statuses = {lane["task_id"]: lane["status"] for lane in body["task_lanes"]}
    assert round_states == {"round-old": "executing", "round-new": "executing"}
    assert lane_statuses == {"task-old": "executing", "task-new": "in_progress"}
    assert round_store.set_run_state_calls == []
    assert round_store.record_task_events_calls == []


def test_returns_middleware_message_rows_as_control_rows():
    """Middleware LLM messages stay persisted but are hidden from chat history."""
    rows = [
        {
            "seq": 1,
            "run_id": "run-1",
            "event_type": "llm.ai.response",
            "category": "message",
            "content": {"type": "ai", "content": "generated title"},
            "metadata": {"caller": "middleware:title"},
        }
    ]
    app = _make_app(event_store=_make_event_store(rows))
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/runs/run-1/messages")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data[0]["content"] == {"type": "ai", "content": "generated title"}
    assert data[0]["display"] == {
        "visible_in_chat": False,
        "surface": "control",
        "reason": "middleware_message",
        "message_type": "system_internal_state",
        "payload_types": [],
    }


def test_list_run_messages_attaches_display_visibility_contract():
    rows = [
        {
            "seq": 1,
            "run_id": "run-1",
            "event_type": "llm.human.input",
            "category": "message",
            "content": {"type": "human", "content": "question"},
            "metadata": {"caller": "lead_agent"},
        },
        {
            "seq": 2,
            "run_id": "run-1",
            "event_type": "llm.ai.response",
            "category": "message",
            "content": {"type": "ai", "content": "answer"},
            "metadata": {"caller": "lead_agent"},
        },
        {
            "seq": 3,
            "run_id": "run-1",
            "event_type": "llm.ai.response",
            "category": "message",
            "content": {"type": "ai", "content": "generated title"},
            "metadata": {"caller": "middleware:title"},
        },
        {
            "seq": 4,
            "run_id": "run-1",
            "event_type": "llm.ai.response",
            "category": "message",
            "content": {"type": "ai", "content": "compressed summary"},
            "metadata": {"caller": "middleware:summarize"},
        },
        {
            "seq": 5,
            "run_id": "run-1",
            "event_type": "llm.tool.result",
            "category": "message",
            "content": {"type": "tool", "content": "tool output", "tool_call_id": "call-1"},
            "metadata": {"caller": "task"},
        },
        {
            "seq": 6,
            "run_id": "run-1",
            "event_type": "llm.system",
            "category": "message",
            "content": {"type": "system", "content": "system prompt"},
            "metadata": {"caller": "lead_agent"},
        },
        {
            "seq": 7,
            "run_id": "run-1",
            "event_type": "task_completed",
            "category": "message",
            "content": {
                "type": "task_completed",
                "task_id": "call-1",
                "thread_id": "thread-1",
                "run_id": "run-1",
                "artifact_refs": ["outputs/report.md"],
                "action_result": {"status": "completed", "summary": "done"},
            },
            "metadata": {"caller": "task_event"},
        },
        {
            "seq": 8,
            "run_id": "run-1",
            "event_type": "llm.ai.response",
            "category": "message",
            "content": {"type": "ai", "content": "hidden", "additional_kwargs": {"hide_from_ui": True}},
            "metadata": {"caller": "lead_agent"},
        },
        {
            "seq": 9,
            "run_id": "run-1",
            "event_type": "llm.human.input",
            "category": "message",
            "content": {"type": "human", "name": "summary", "content": "summary"},
            "metadata": {"caller": "lead_agent"},
        },
    ]
    app = _make_app(event_store=_make_event_store(rows))
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/runs/run-1/messages")

    assert response.status_code == 200
    displays = [row["display"] for row in response.json()["data"]]
    assert displays == [
        {"visible_in_chat": True, "surface": "chat", "reason": "human_message", "message_type": "visible_chat_message", "payload_types": []},
        {"visible_in_chat": True, "surface": "chat", "reason": "lead_ai_response", "message_type": "visible_chat_message", "payload_types": []},
        {"visible_in_chat": False, "surface": "control", "reason": "middleware_message", "message_type": "system_internal_state", "payload_types": []},
        {"visible_in_chat": False, "surface": "control", "reason": "middleware_message", "message_type": "system_internal_state", "payload_types": []},
        {"visible_in_chat": False, "surface": "control", "reason": "tool_message", "message_type": "system_internal_state", "payload_types": []},
        {"visible_in_chat": False, "surface": "control", "reason": "control_message", "message_type": "system_internal_state", "payload_types": []},
        {
            "visible_in_chat": False,
            "surface": "control",
            "reason": "task_event",
            "message_type": "task_event",
            "payload_types": ["action_result", "artifact_reference"],
        },
        {"visible_in_chat": False, "surface": "hidden", "reason": "hide_from_ui", "message_type": "system_internal_state", "payload_types": []},
        {"visible_in_chat": False, "surface": "control", "reason": "control_message", "message_type": "system_internal_state", "payload_types": []},
    ]


def test_has_more_true_when_extra_row_returned():
    """has_more=True when event store returns limit+1 rows."""
    # Default limit is 50; provide 51 rows
    rows = [_make_message(i) for i in range(1, 52)]  # 51 rows
    app = _make_app(event_store=_make_event_store(rows))
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-2/runs/run-2/messages")
    assert response.status_code == 200
    body = response.json()
    assert body["has_more"] is True
    assert len(body["data"]) == 50  # trimmed to limit
    assert [m["seq"] for m in body["data"]] == list(range(2, 52))


def test_default_page_keeps_newest_messages_when_extra_row_returned():
    """Default latest-page trimming drops the older sentinel row, not the newest message."""
    rows = [_make_message(i) for i in range(16, 67)]
    app = _make_app(event_store=_make_event_store(rows))
    with TestClient(app) as client:
        assert_run_message_page(
            client,
            "/api/threads/thread-2/runs/run-2/messages",
            expected_seq=list(range(17, 67)),
        )


def test_before_seq_page_keeps_newest_side_when_extra_row_returned():
    """Backward pagination trims the older sentinel so adjacent pages do not miss the boundary message."""
    rows = [_make_message(i) for i in range(1, 18)]
    app = _make_app(event_store=_make_event_store(rows))
    with TestClient(app) as client:
        assert_run_message_page(
            client,
            "/api/threads/thread-2/runs/run-2/messages?before_seq=18&limit=16",
            expected_seq=list(range(2, 18)),
        )


def test_after_seq_page_keeps_oldest_side_when_extra_row_returned():
    """Forward pagination still trims the newer sentinel row."""
    rows = [_make_message(i) for i in range(11, 62)]
    app = _make_app(event_store=_make_event_store(rows))
    with TestClient(app) as client:
        assert_run_message_page(
            client,
            "/api/threads/thread-2/runs/run-2/messages?after_seq=10",
            expected_seq=list(range(11, 61)),
        )


def test_after_seq_forwarded_to_event_store():
    """after_seq query param is forwarded to event_store.list_messages_by_run."""
    rows = [_make_message(10)]
    event_store = _make_event_store(rows)
    app = _make_app(event_store=event_store)
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-3/runs/run-3/messages?after_seq=5")
    assert response.status_code == 200
    event_store.list_messages_by_run.assert_awaited_once_with(
        "thread-3",
        "run-3",
        limit=51,  # default limit(50) + 1
        before_seq=None,
        after_seq=5,
        user_id=str(_TEST_USER_ID),
    )


def test_before_seq_forwarded_to_event_store():
    """before_seq query param is forwarded to event_store.list_messages_by_run."""
    rows = [_make_message(3)]
    event_store = _make_event_store(rows)
    app = _make_app(event_store=event_store)
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-4/runs/run-4/messages?before_seq=10")
    assert response.status_code == 200
    event_store.list_messages_by_run.assert_awaited_once_with(
        "thread-4",
        "run-4",
        limit=51,
        before_seq=10,
        after_seq=None,
        user_id=str(_TEST_USER_ID),
    )


def test_custom_limit_forwarded_to_event_store():
    """Custom limit is forwarded as limit+1 to the event store."""
    rows = [_make_message(i) for i in range(1, 6)]
    event_store = _make_event_store(rows)
    app = _make_app(event_store=event_store)
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-5/runs/run-5/messages?limit=10")
    assert response.status_code == 200
    event_store.list_messages_by_run.assert_awaited_once_with(
        "thread-5",
        "run-5",
        limit=11,  # 10 + 1
        before_seq=None,
        after_seq=None,
        user_id=str(_TEST_USER_ID),
    )


def test_empty_data_when_no_messages():
    """Returns empty data list with has_more=False when no messages exist."""
    app = _make_app(event_store=_make_event_store([]))
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-6/runs/run-6/messages")
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == []
    assert body["has_more"] is False


def test_list_run_messages_scopes_events_to_current_user():
    user_a = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    user_b = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    thread_id = "shared-thread"
    run_id = "shared-run"

    event_store = MagicMock()

    async def list_messages_by_run(thread_id_arg, run_id_arg, *, limit=50, before_seq=None, after_seq=None, user_id=None):
        assert thread_id_arg == thread_id
        assert run_id_arg == run_id
        assert limit == 51
        assert before_seq is None
        assert after_seq is None
        if user_id == str(user_a):
            return [{"seq": 1, "run_id": run_id, "event_type": "llm.ai.response", "category": "message", "content": "owner-a"}]
        if user_id == str(user_b):
            return [{"seq": 1, "run_id": run_id, "event_type": "llm.ai.response", "category": "message", "content": "owner-b"}]
        return [{"seq": 1, "run_id": run_id, "event_type": "llm.ai.response", "category": "message", "content": "owner-b"}]

    event_store.list_messages_by_run = AsyncMock(side_effect=list_messages_by_run)

    run_manager = AsyncMock()
    run_manager.get.return_value = RunRecord(
        run_id=run_id,
        thread_id=thread_id,
        assistant_id=None,
        status=RunStatus.success,
        on_disconnect="cancel",
        user_id=str(user_a),
    )

    app = _make_app(event_store=event_store, run_manager=run_manager, user_id=user_a)

    with TestClient(app) as client:
        response = client.get(f"/api/threads/{thread_id}/runs/{run_id}/messages")

    assert response.status_code == 200
    assert response.json()["data"][0]["content"] == "owner-a"
    run_manager.get.assert_awaited_once_with(run_id, user_id=str(user_a))
    event_store.list_messages_by_run.assert_awaited_once_with(
        thread_id,
        run_id,
        limit=51,
        before_seq=None,
        after_seq=None,
        user_id=str(user_a),
    )


def test_list_run_events_scopes_events_to_current_user():
    user_a = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    user_b = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    thread_id = "shared-thread"
    run_id = "shared-run"

    event_store = MagicMock()

    async def list_events(thread_id_arg, run_id_arg, *, event_types=None, limit=500, after_seq=None, user_id=None):
        assert thread_id_arg == thread_id
        assert run_id_arg == run_id
        assert event_types == ["llm.context"]
        assert limit == 7
        assert after_seq is None
        if user_id == str(user_a):
            return [{"seq": 1, "run_id": run_id, "event_type": "llm.context", "content": {"owner": "a"}}]
        if user_id == str(user_b):
            return [{"seq": 1, "run_id": run_id, "event_type": "llm.context", "content": {"owner": "b"}}]
        return [{"seq": 1, "run_id": run_id, "event_type": "llm.context", "content": {"owner": "b"}}]

    event_store.list_events = AsyncMock(side_effect=list_events)

    run_manager = AsyncMock()
    run_manager.get.return_value = RunRecord(
        run_id=run_id,
        thread_id=thread_id,
        assistant_id=None,
        status=RunStatus.success,
        on_disconnect="cancel",
        user_id=str(user_a),
    )

    app = _make_app(event_store=event_store, run_manager=run_manager, user_id=user_a)

    with TestClient(app) as client:
        response = client.get(f"/api/threads/{thread_id}/runs/{run_id}/events?event_types=llm.context&limit=7")

    assert response.status_code == 200
    assert response.json()[0]["content"]["owner"] == "a"
    run_manager.get.assert_awaited_once_with(run_id, user_id=str(user_a))
    event_store.list_events.assert_awaited_once_with(
        thread_id,
        run_id,
        event_types=["llm.context"],
        limit=7,
        after_seq=None,
        user_id=str(user_a),
    )


def test_list_run_events_passes_after_seq_to_event_store():
    thread_id = "thread-7"
    run_id = "run-7"
    event_store = MagicMock()
    event_store.list_events = AsyncMock(return_value=[{"seq": 8, "run_id": run_id, "event_type": "run.end"}])

    run_manager = AsyncMock()
    run_manager.get.return_value = RunRecord(
        run_id=run_id,
        thread_id=thread_id,
        assistant_id=None,
        status=RunStatus.success,
        on_disconnect="cancel",
        user_id=str(_TEST_USER_ID),
    )
    app = _make_app(event_store=event_store, run_manager=run_manager)

    with TestClient(app) as client:
        response = client.get(f"/api/threads/{thread_id}/runs/{run_id}/events?after_seq=5&limit=9")

    assert response.status_code == 200
    event_store.list_events.assert_awaited_once_with(
        thread_id,
        run_id,
        event_types=None,
        limit=9,
        after_seq=5,
        user_id=str(_TEST_USER_ID),
    )


def test_event_and_evidence_limits_reject_non_positive_values_before_store_io():
    thread_id = "thread-bounded"
    run_id = "run-bounded"
    event_store = MagicMock()
    event_store.list_events = AsyncMock(return_value=[])
    run_manager = AsyncMock()
    run_manager.get.return_value = RunRecord(
        run_id=run_id,
        thread_id=thread_id,
        assistant_id=None,
        status=RunStatus.success,
        on_disconnect="cancel",
        user_id=str(_TEST_USER_ID),
    )
    app = _make_app(event_store=event_store, run_manager=run_manager)

    with TestClient(app) as client:
        events = client.get(f"/api/threads/{thread_id}/runs/{run_id}/events?limit=0")
        evidence = client.get(f"/api/threads/{thread_id}/runs/{run_id}/evidence?limit=-1")

    assert events.status_code == 422
    assert evidence.status_code == 422
    event_store.list_events.assert_not_awaited()


def test_run_thread_mismatch_returns_404_without_reading_events():
    """The run resolver must reject mismatched thread/run pairs before event reads."""
    rows = [_make_message(1)]
    event_store = _make_event_store(rows)
    run_manager = AsyncMock()
    run_manager.get.return_value = RunRecord(
        run_id="run-1",
        thread_id="thread-other",
        assistant_id=None,
        status=RunStatus.success,
        on_disconnect="cancel",
    )
    app = _make_app(event_store=event_store, run_manager=run_manager)

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/runs/run-1/messages")

    assert response.status_code == 404
    event_store.list_messages_by_run.assert_not_awaited()


def test_get_run_hydrates_store_only_run():
    """GET /api/threads/{tid}/runs/{rid} should read historical store rows."""
    app = _make_app(run_manager=_make_store_only_run_manager())
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-store/runs/store-only-run")

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "store-only-run"
    assert body["thread_id"] == "thread-store"
    assert body["status"] == "running"


def test_cancel_store_only_run_records_durable_cancel_intent():
    """Store-only running runs accept a durable cancel intent for their owner worker."""
    run_manager = _make_store_only_run_manager()
    app = _make_app(run_manager=run_manager)
    with TestClient(app) as client:
        response = client.post("/api/threads/thread-store/runs/store-only-run/cancel")

    assert response.status_code == 202
    row = asyncio.run(run_manager._store.get("store-only-run"))  # type: ignore[union-attr]
    assert row["cancel_action"] == "interrupt"
    assert row["cancellation_requested_at"]


def test_join_store_only_run_returns_409():
    """join endpoint should return 409 for store-only runs (no local stream state)."""
    app = _make_app(run_manager=_make_store_only_run_manager())
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-store/runs/store-only-run/join")

    assert response.status_code == 409
    assert "not active on this worker" in response.json()["detail"]


def test_join_local_terminal_run_streams_end():
    """join should accept the thread route's durable replay context kwargs."""
    thread_id = "thread-join"
    run_id = "run-join"
    run_manager = AsyncMock()
    run_manager.get.return_value = RunRecord(
        run_id=run_id,
        thread_id=thread_id,
        assistant_id=None,
        status=RunStatus.success,
        on_disconnect="continue",
        user_id=str(_TEST_USER_ID),
    )
    event_store = AsyncMock()
    event_store.list_events.return_value = []
    app = _make_app(event_store=event_store, run_manager=run_manager)
    app.state.stream_bridge = MemoryStreamBridge()

    with TestClient(app) as client:
        response = client.get(f"/api/threads/{thread_id}/runs/{run_id}/join")

    assert response.status_code == 200
    assert response.text.startswith("event: custom\n")
    assert '"event_type": "run.terminal"' in response.text
    assert response.text.endswith("event: end\ndata: null\n\n")


def test_stream_store_only_run_returns_409():
    """stream endpoint (action=None) should return 409 for store-only runs."""
    app = _make_app(run_manager=_make_store_only_run_manager())
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-store/runs/store-only-run/stream")

    assert response.status_code == 409
    assert "not active on this worker" in response.json()["detail"]


def test_stream_store_only_rolling_back_run_returns_409():
    """Store-only cancel-in-progress runs are still active on another worker."""
    app = _make_app(run_manager=_make_store_only_run_manager("rolling_back"))
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-store/runs/store-only-run/stream")

    assert response.status_code == 409
    assert "not active on this worker" in response.json()["detail"]


def test_stream_store_only_terminal_run_replays_persisted_events_after_restart():
    """Terminal store-only runs can be replayed after worker-local stream state is gone."""
    thread_id = "thread-restart"
    run_id = "run-restart"
    user_id = str(_TEST_USER_ID)
    run_store = MemoryRunStore()
    event_store = MemoryRunEventStore()
    task_event = {
        "schema_version": "deerflow.task-event/v1",
        "type": "task_completed",
        "event_type": "task_completed",
        "thread_id": thread_id,
        "run_id": run_id,
        "task_id": "task-restart",
        "status": "completed",
    }
    asyncio.run(
        run_store.put(
            run_id,
            thread_id=thread_id,
            assistant_id="lead_agent",
            user_id=user_id,
            status="success",
            multitask_strategy="reject",
            metadata={},
            kwargs={},
            created_at="2026-01-01T00:00:00+00:00",
        )
    )
    asyncio.run(
        event_store.put(
            thread_id=thread_id,
            run_id=run_id,
            event_type="task_completed",
            category="message",
            content=task_event,
            metadata={"caller": "task_event"},
            user_id=user_id,
        )
    )
    asyncio.run(
        event_store.put(
            thread_id=thread_id,
            run_id=run_id,
            event_type="run.terminal",
            category="lifecycle",
            content={"status": "success", "terminal_reason": "success"},
            metadata={"caller": "runtime"},
            user_id=user_id,
        )
    )
    app = _make_app(event_store=event_store, run_manager=RunManager(store=run_store))
    app.state.stream_bridge = MemoryStreamBridge()

    with TestClient(app) as client:
        response = client.get(f"/api/threads/{thread_id}/runs/{run_id}/stream")

    assert response.status_code == 200
    frames = [frame for frame in response.text.split("\n\n") if frame]
    assert [frame.splitlines()[0] for frame in frames] == ["event: custom", "event: custom", "event: end"]
    assert json.loads(frames[0].split("data: ", 1)[1].splitlines()[0]) == task_event
    assert json.loads(frames[1].split("data: ", 1)[1].splitlines()[0]) == {
        "type": "run.terminal",
        "event_type": "run.terminal",
        "thread_id": thread_id,
        "run_id": run_id,
        "status": "success",
        "terminal_reason": "success",
    }


def test_compute_run_durations_prefers_terminal_completion_time():
    run = RunRecord(
        run_id="run-1",
        thread_id="thread-1",
        assistant_id=None,
        status="success",
        on_disconnect="cancel",
        created_at="2026-06-20T10:00:00Z",
        updated_at="2026-07-20T10:00:00Z",
        metadata={"completed_at": "2026-06-20T10:00:05Z"},
    )

    assert thread_runs.compute_run_durations([run]) == {"run-1": 5}


def test_list_run_messages_injects_turn_duration():
    """Verify that list_run_messages injects turn_duration into ALL AI messages for the run."""
    from unittest.mock import AsyncMock

    from deerflow.runtime import RunRecord

    # Mock a run record that took exactly 5 seconds
    mock_run = RunRecord(
        run_id="run-1",
        thread_id="thread-1",
        assistant_id=None,
        status="success",
        on_disconnect="cancel",
        created_at="2026-06-20T10:00:00Z",
        updated_at="2026-06-20T10:00:05Z",
    )

    rows = [
        {"seq": 1, "run_id": "run-1", "content": {"type": "human", "text": "Hello"}},
        {"seq": 2, "run_id": "run-1", "content": {"type": "ai", "text": "Thinking..."}},
        {"seq": 3, "run_id": "run-1", "content": {"type": "ai", "text": "Response"}},
    ]

    event_store = _make_event_store(rows)
    run_manager = AsyncMock()
    run_manager.get.return_value = mock_run
    app = _make_app(event_store=event_store, run_manager=run_manager)

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/runs/run-1/messages")

    assert response.status_code == 200
    data = response.json()["data"]

    assert "turn_duration" not in data[0]["content"].get("additional_kwargs", {})

    assert data[1]["content"]["additional_kwargs"]["turn_duration"] == 5
    assert data[2]["content"]["additional_kwargs"]["turn_duration"] == 5


def test_list_thread_messages_injects_turn_duration():
    """Verify that list_thread_messages injects turn_duration into the inner content."""
    from unittest.mock import AsyncMock

    from deerflow.runtime import RunRecord

    mock_run = RunRecord(
        run_id="run-1",
        thread_id="thread-1",
        assistant_id=None,
        status="success",
        on_disconnect="cancel",
        created_at="2026-06-20T10:00:00Z",
        updated_at="2026-06-20T10:00:05Z",
    )
    rows = [
        {"seq": 1, "run_id": "run-1", "content": {"type": "human", "text": "Hello"}},
        {"seq": 2, "run_id": "run-1", "content": {"type": "ai", "text": "Response"}},
    ]

    event_store = MagicMock()
    event_store.list_messages = AsyncMock(return_value=rows)

    run_manager = AsyncMock()
    run_manager.list_by_thread = AsyncMock(return_value=[mock_run])

    feedback_repo = MagicMock()
    feedback_repo.list_by_thread_grouped = AsyncMock(return_value={})

    app = _make_app(event_store=event_store, run_manager=run_manager)
    app.state.feedback_repo = feedback_repo

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/messages")

    assert response.status_code == 200
    data = response.json()

    assert "turn_duration" not in data[0].get("content", {}).get("additional_kwargs", {})
    assert data[1]["content"]["additional_kwargs"]["turn_duration"] == 5


def test_list_thread_messages_attaches_display_visibility_contract():
    rows = [
        {
            "seq": 1,
            "run_id": "run-1",
            "event_type": "llm.ai.response",
            "category": "message",
            "content": {"type": "ai", "content": "answer"},
            "metadata": {"caller": "lead_agent"},
        },
        {
            "seq": 2,
            "run_id": "run-1",
            "event_type": "task_running",
            "category": "message",
            "content": {
                "type": "task_running",
                "task_id": "call-1",
                "thread_id": "thread-1",
                "run_id": "run-1",
            },
            "metadata": {"caller": "task_event"},
        },
    ]
    event_store = MagicMock()
    event_store.list_messages = AsyncMock(return_value=rows)
    run_manager = MagicMock()
    run_manager.list_by_thread = AsyncMock(return_value=[])
    feedback_repo = MagicMock()
    feedback_repo.list_by_thread_grouped = AsyncMock(return_value={})
    app = _make_app(event_store=event_store, run_manager=run_manager)
    app.state.feedback_repo = feedback_repo

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/messages")

    assert response.status_code == 200
    data = response.json()
    assert data[0]["display"] == {
        "visible_in_chat": True,
        "surface": "chat",
        "reason": "lead_ai_response",
        "message_type": "visible_chat_message",
        "payload_types": [],
    }
    assert data[1]["display"] == {
        "visible_in_chat": False,
        "surface": "control",
        "reason": "task_event",
        "message_type": "task_event",
        "payload_types": [],
    }


def test_list_thread_messages_projects_background_chair_update_to_round_summary():
    rows = [
        {
            "seq": 1,
            "run_id": "run-1",
            "event_type": "llm.tool.result",
            "category": "message",
            "content": {
                "type": "tool",
                "name": "task",
                "content": "accepted",
                "additional_kwargs": {"background_task": True},
            },
            "metadata": {"caller": "lead_agent"},
        },
        {
            "seq": 2,
            "run_id": "run-1",
            "event_type": "llm.ai.response",
            "category": "message",
            "content": {"type": "ai", "content": "Stage update"},
            "metadata": {"caller": "lead_agent"},
        },
    ]
    event_store = MagicMock()
    event_store.list_messages = AsyncMock(return_value=rows)
    run_manager = MagicMock()
    run_manager.list_by_thread = AsyncMock(return_value=[])
    feedback_repo = MagicMock()
    feedback_repo.list_by_thread_grouped = AsyncMock(return_value={})
    app = _make_app(event_store=event_store, run_manager=run_manager)
    app.state.feedback_repo = feedback_repo

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/messages")

    assert response.status_code == 200
    data = response.json()
    assert data[1]["display"] == {
        "visible_in_chat": False,
        "surface": "audit",
        "reason": "command_room_step",
        "message_type": "round_summary",
        "payload_types": [],
    }


def test_list_thread_messages_rejects_non_positive_limit():
    event_store = _make_event_store([])
    run_manager = MagicMock()
    run_manager.list_by_thread = AsyncMock(return_value=[])
    app = _make_app(event_store=event_store, run_manager=run_manager)
    app.state.feedback_repo = SimpleNamespace(list_by_thread_grouped=AsyncMock(return_value={}))

    with TestClient(app) as client:
        negative = client.get("/api/threads/thread-1/messages?limit=-1")
        zero = client.get("/api/threads/thread-1/messages?limit=0")

    assert negative.status_code == 422
    assert zero.status_code == 422
    event_store.list_messages.assert_not_awaited()


def test_list_thread_messages_scopes_events_to_current_user():
    user_a = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    user_b = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    thread_id = "shared-thread"

    event_store = MagicMock()

    async def list_messages(thread_id_arg, *, limit=50, before_seq=None, after_seq=None, user_id=None):
        assert thread_id_arg == thread_id
        assert limit == 50
        assert before_seq is None
        assert after_seq is None
        if user_id == str(user_a):
            return [
                {
                    "seq": 1,
                    "run_id": "run-a",
                    "event_type": "llm.ai.response",
                    "category": "message",
                    "content": {"type": "ai", "text": "owner-a"},
                }
            ]
        if user_id == str(user_b):
            return [
                {
                    "seq": 1,
                    "run_id": "run-b",
                    "event_type": "llm.ai.response",
                    "category": "message",
                    "content": {"type": "ai", "text": "owner-b"},
                }
            ]
        return [
            {
                "seq": 1,
                "run_id": "run-b",
                "event_type": "llm.ai.response",
                "category": "message",
                "content": {"type": "ai", "text": "owner-b"},
            }
        ]

    event_store.list_messages = AsyncMock(side_effect=list_messages)

    run_manager = MagicMock()
    run_manager.list_by_thread = AsyncMock(return_value=[])
    feedback_repo = MagicMock()
    feedback_repo.list_by_thread_grouped = AsyncMock(return_value={})

    app = _make_app(event_store=event_store, run_manager=run_manager, user_id=user_a)
    app.state.feedback_repo = feedback_repo

    with TestClient(app) as client:
        response = client.get(f"/api/threads/{thread_id}/messages")

    assert response.status_code == 200
    assert response.json()[0]["content"]["text"] == "owner-a"
    event_store.list_messages.assert_awaited_once_with(
        thread_id,
        limit=50,
        before_seq=None,
        after_seq=None,
        user_id=str(user_a),
    )
    feedback_repo.list_by_thread_grouped.assert_awaited_once_with(thread_id, user_id=str(user_a))
    run_manager.list_by_thread.assert_awaited_once_with(thread_id, user_id=str(user_a))


def test_list_runs_uses_trusted_internal_owner_header():
    from app.gateway.internal_auth import INTERNAL_OWNER_USER_ID_HEADER_NAME, INTERNAL_SYSTEM_ROLE

    run_manager = MagicMock()
    run_manager.list_by_thread = AsyncMock(
        return_value=[
            RunRecord(
                run_id="run-owner",
                thread_id="thread-1",
                assistant_id=None,
                status=RunStatus.success,
                on_disconnect="cancel",
            )
        ]
    )
    request = SimpleNamespace(
        headers={INTERNAL_OWNER_USER_ID_HEADER_NAME: "owner-1"},
        state=SimpleNamespace(user=SimpleNamespace(id="default", system_role=INTERNAL_SYSTEM_ROLE)),
        app=SimpleNamespace(state=SimpleNamespace(run_manager=run_manager)),
    )

    async def _scenario():
        return await call_unwrapped(thread_runs.list_runs, "thread-1", request)

    response = asyncio.run(_scenario())

    run_manager.list_by_thread.assert_awaited_once_with(
        "thread-1",
        user_id="owner-1",
        limit=100,
    )
    assert response[0].run_id == "run-owner"


def test_list_runs_supports_cursor_pagination_for_older_history():
    thread_id = "thread-run-history"
    user_id = str(_TEST_USER_ID)
    run_store = MemoryRunStore()

    async def _seed_runs() -> None:
        for index in range(105):
            await run_store.put(
                f"run-{index:03d}",
                thread_id=thread_id,
                user_id=user_id,
                status="success",
                created_at=f"2026-01-01T00:{index // 60:02d}:{index % 60:02d}+00:00",
            )

    asyncio.run(_seed_runs())
    app = _make_app(run_manager=RunManager(store=run_store))

    with TestClient(app) as client:
        response = client.get(f"/api/threads/{thread_id}/runs?limit=5&before=run-005")

    assert response.status_code == 200
    assert [row["run_id"] for row in response.json()] == [
        "run-004",
        "run-003",
        "run-002",
        "run-001",
        "run-000",
    ]


@pytest.mark.asyncio
async def test_run_history_cursor_is_stable_when_newer_run_is_inserted():
    store = MemoryRunStore()
    manager = RunManager(store=store)
    thread_id = "thread-cursor-stability"
    for index in range(1, 4):
        await store.put(
            f"run-{index}",
            thread_id=thread_id,
            status="success",
            created_at=f"2026-01-01T00:00:0{index}+00:00",
        )

    first_page = await manager.list_by_thread(thread_id, limit=2)
    await store.put(
        "run-4",
        thread_id=thread_id,
        status="success",
        created_at="2026-01-01T00:00:04+00:00",
    )
    second_page = await manager.list_by_thread(
        thread_id,
        limit=2,
        before=first_page[-1].run_id,
    )

    assert [record.run_id for record in first_page] == ["run-3", "run-2"]
    assert [record.run_id for record in second_page] == ["run-1"]


@pytest.mark.asyncio
async def test_run_history_cursor_has_stable_tie_breaker_for_equal_timestamps():
    store = MemoryRunStore()
    manager = RunManager(store=store)
    thread_id = "thread-cursor-ties"
    for run_id in ("run-a", "run-b", "run-c"):
        await store.put(
            run_id,
            thread_id=thread_id,
            status="success",
            created_at="2026-01-01T00:00:00+00:00",
        )

    first_page = await manager.list_by_thread(thread_id, limit=1)
    second_page = await manager.list_by_thread(
        thread_id,
        limit=1,
        before=first_page[-1].run_id,
    )

    assert [record.run_id for record in first_page] == ["run-c"]
    assert [record.run_id for record in second_page] == ["run-b"]


def test_list_thread_messages_run_id_filters_to_requested_run():
    thread_id = "thread-shared"
    run_a = "run-a"
    run_b = "run-b"
    rows_all = [
        {"seq": 1, "run_id": run_a, "event_type": "llm.ai.response", "category": "message", "content": {"type": "ai", "content": "a"}},
        {"seq": 2, "run_id": run_b, "event_type": "llm.ai.response", "category": "message", "content": {"type": "ai", "content": "b"}},
    ]
    rows_a = [rows_all[0]]
    event_store = MagicMock()
    event_store.list_messages = AsyncMock(return_value=rows_all)
    event_store.list_messages_by_run = AsyncMock(return_value=rows_a)

    run_manager = AsyncMock()
    run_manager.get.return_value = RunRecord(
        run_id=run_a,
        thread_id=thread_id,
        assistant_id=None,
        status=RunStatus.success,
        on_disconnect="cancel",
        user_id=str(_TEST_USER_ID),
    )
    run_manager.list_by_thread.return_value = [
        RunRecord(run_id=run_a, thread_id=thread_id, assistant_id=None, status=RunStatus.success, on_disconnect="cancel"),
        RunRecord(run_id=run_b, thread_id=thread_id, assistant_id=None, status=RunStatus.success, on_disconnect="cancel"),
    ]
    app = _make_app(event_store=event_store, run_manager=run_manager)
    app.state.feedback_repo = AsyncMock()
    app.state.feedback_repo.list_by_thread_grouped.return_value = {}

    with TestClient(app) as client:
        scoped = client.get(f"/api/threads/{thread_id}/messages?run_id={run_a}")
        unscoped = client.get(f"/api/threads/{thread_id}/messages")

    assert scoped.status_code == 200
    assert [row["run_id"] for row in scoped.json()] == [run_a]
    event_store.list_messages_by_run.assert_awaited_once_with(
        thread_id,
        run_a,
        limit=50,
        before_seq=None,
        after_seq=None,
        user_id=str(_TEST_USER_ID),
    )
    assert unscoped.status_code == 200
    assert [row["run_id"] for row in unscoped.json()] == [run_a, run_b]
    event_store.list_messages.assert_awaited_once_with(
        thread_id,
        limit=50,
        before_seq=None,
        after_seq=None,
        user_id=str(_TEST_USER_ID),
    )


def test_list_thread_messages_run_id_thread_mismatch_returns_404_without_reading_messages():
    event_store = _make_event_store([])
    run_manager = AsyncMock()
    run_manager.get.return_value = RunRecord(
        run_id="run-a",
        thread_id="thread-other",
        assistant_id=None,
        status=RunStatus.success,
        on_disconnect="cancel",
        user_id=str(_TEST_USER_ID),
    )
    app = _make_app(event_store=event_store, run_manager=run_manager)

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-a/messages?run_id=run-a")

    assert response.status_code == 404
    event_store.list_messages_by_run.assert_not_awaited()
    event_store.list_messages.assert_not_awaited()


def test_list_thread_messages_run_id_preserves_after_seq_forwarding():
    rows = [{"seq": 6, "run_id": "run-6", "event_type": "llm.ai.response", "category": "message", "content": "msg-6"}]
    event_store = _make_event_store(rows)
    run_manager = AsyncMock()
    run_manager.get.return_value = RunRecord(
        run_id="run-6",
        thread_id="thread-6",
        assistant_id=None,
        status=RunStatus.success,
        on_disconnect="cancel",
        user_id=str(_TEST_USER_ID),
    )
    app = _make_app(event_store=event_store, run_manager=run_manager)
    app.state.feedback_repo = AsyncMock()
    app.state.feedback_repo.list_by_thread_grouped.return_value = {}

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-6/messages?run_id=run-6&after_seq=5&limit=10")

    assert response.status_code == 200
    event_store.list_messages_by_run.assert_awaited_once_with(
        "thread-6",
        "run-6",
        limit=10,
        before_seq=None,
        after_seq=5,
        user_id=str(_TEST_USER_ID),
    )
