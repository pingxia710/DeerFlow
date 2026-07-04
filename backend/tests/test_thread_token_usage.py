"""Tests for thread-level token usage aggregation API."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers import thread_runs

_TEST_USER_ID = UUID("11111111-1111-1111-1111-111111111111")


def _make_user(user_id: UUID = _TEST_USER_ID) -> User:
    return User(id=user_id, email=f"{user_id}@example.com", password_hash="x", system_role="user")


def _make_app(run_store: MagicMock, event_store: MagicMock | None = None, *, user_id: UUID = _TEST_USER_ID):
    app = make_authed_test_app(user_factory=lambda: _make_user(user_id))
    app.include_router(thread_runs.router)
    app.state.run_store = run_store
    app.state.run_event_store = event_store or MagicMock()
    return app


def test_thread_token_usage_returns_stable_shape():
    run_store = MagicMock()
    run_store.aggregate_tokens_by_thread = AsyncMock(
        return_value={
            "total_tokens": 150,
            "total_input_tokens": 90,
            "total_output_tokens": 60,
            "total_runs": 2,
            "by_model": {"unknown": {"tokens": 150, "runs": 2}},
            "by_caller": {
                "lead_agent": 120,
                "subagent": 25,
                "middleware": 5,
            },
        },
    )
    app = _make_app(run_store)

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/token-usage")

    assert response.status_code == 200
    assert response.json() == {
        "thread_id": "thread-1",
        "total_tokens": 150,
        "total_input_tokens": 90,
        "total_output_tokens": 60,
        "total_runs": 2,
        "by_model": {"unknown": {"tokens": 150, "runs": 2}},
        "by_caller": {
            "lead_agent": 120,
            "subagent": 25,
            "middleware": 5,
        },
    }
    run_store.aggregate_tokens_by_thread.assert_awaited_once_with("thread-1", user_id=str(_TEST_USER_ID))


def test_thread_token_usage_can_include_active_runs():
    run_store = MagicMock()
    run_store.aggregate_tokens_by_thread = AsyncMock(
        return_value={
            "total_tokens": 175,
            "total_input_tokens": 120,
            "total_output_tokens": 55,
            "total_runs": 3,
            "by_model": {"unknown": {"tokens": 175, "runs": 3}},
            "by_caller": {
                "lead_agent": 145,
                "subagent": 25,
                "middleware": 5,
            },
        },
    )
    app = _make_app(run_store)

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/token-usage?include_active=true")

    assert response.status_code == 200
    assert response.json()["total_tokens"] == 175
    assert response.json()["total_runs"] == 3
    run_store.aggregate_tokens_by_thread.assert_awaited_once_with("thread-1", include_active=True, user_id=str(_TEST_USER_ID))


def test_thread_context_usage_returns_latest_snapshots():
    run_store = MagicMock()
    run_store.list_by_thread = AsyncMock(
        return_value=[
            {"run_id": "run-2"},
            {"run_id": "run-1"},
        ],
    )
    event_store = MagicMock()
    event_store.list_events = AsyncMock(
        side_effect=[
            [
                {
                    "run_id": "run-2",
                    "event_type": "llm.context",
                    "content": {
                        "caller": "subagent:research",
                        "llm_call_index": 1,
                        "message_count": 3,
                        "char_count": 120,
                        "estimated_tokens": 30,
                        "role_counts": {"human": 1, "ai": 2},
                    },
                    "seq": 4,
                    "created_at": "2026-06-28T10:01:00+00:00",
                },
                {
                    "run_id": "run-2",
                    "event_type": "llm.context",
                    "content": {
                        "caller": "lead_agent",
                        "llm_call_index": 2,
                        "message_count": 8,
                        "char_count": 400,
                        "estimated_tokens": 100,
                        "role_counts": {"system": 1, "human": 3, "ai": 4},
                    },
                    "seq": 5,
                    "created_at": "2026-06-28T10:02:00+00:00",
                },
            ],
            [
                {
                    "run_id": "run-1",
                    "event_type": "llm.context",
                    "content": {
                        "caller": "lead_agent",
                        "llm_call_index": 1,
                        "message_count": 4,
                        "char_count": 200,
                        "estimated_tokens": 50,
                        "role_counts": {"human": 2, "ai": 2},
                    },
                    "seq": 2,
                    "created_at": "2026-06-28T10:00:00+00:00",
                }
            ],
        ],
    )
    app = _make_app(run_store, event_store)

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/context-usage")

    assert response.status_code == 200
    data = response.json()
    assert data["latest"]["run_id"] == "run-2"
    assert data["latest"]["estimated_tokens"] == 100
    assert data["latest_lead"]["estimated_tokens"] == 100
    assert data["by_caller"]["subagent:research"]["estimated_tokens"] == 30
    assert [item["run_id"] for item in data["recent"]] == ["run-2", "run-2", "run-1"]
    run_store.list_by_thread.assert_awaited_once_with("thread-1", user_id=str(_TEST_USER_ID), limit=20)
    event_store.list_events.assert_any_await(
        "thread-1",
        "run-2",
        event_types=["llm.context"],
        limit=200,
        user_id=str(_TEST_USER_ID),
    )
    event_store.list_events.assert_any_await(
        "thread-1",
        "run-1",
        event_types=["llm.context"],
        limit=200,
        user_id=str(_TEST_USER_ID),
    )


def test_thread_context_usage_scopes_runs_and_events_to_current_user():
    user_a = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    user_b = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    thread_id = "shared-thread-id"

    run_store = MagicMock()

    async def list_by_thread(thread_id_arg, *, user_id=None, limit=20):
        assert thread_id_arg == thread_id
        assert limit == 20
        if user_id == str(user_a):
            return [{"run_id": "run-a"}]
        if user_id == str(user_b):
            return [{"run_id": "run-b"}]
        return [{"run_id": "run-b"}]

    run_store.list_by_thread = AsyncMock(side_effect=list_by_thread)

    event_store = MagicMock()

    async def list_events(thread_id_arg, run_id, *, event_types=None, limit=500, user_id=None):
        assert thread_id_arg == thread_id
        assert event_types == ["llm.context"]
        assert limit == 200
        if user_id == str(user_a) and run_id == "run-a":
            return [
                {
                    "run_id": "run-a",
                    "event_type": "llm.context",
                    "content": {
                        "caller": "lead_agent",
                        "llm_call_index": 1,
                        "message_count": 2,
                        "char_count": 44,
                        "estimated_tokens": 11,
                        "role_counts": {"human": 1, "ai": 1},
                    },
                    "seq": 1,
                    "created_at": "2026-07-04T10:00:00+00:00",
                }
            ]
        return [
            {
                "run_id": "run-b",
                "event_type": "llm.context",
                "content": {
                    "caller": "lead_agent",
                    "llm_call_index": 1,
                    "message_count": 20,
                    "char_count": 400,
                    "estimated_tokens": 99,
                    "role_counts": {"human": 10, "ai": 10},
                },
                "seq": 1,
                "created_at": "2026-07-04T10:01:00+00:00",
            }
        ]

    event_store.list_events = AsyncMock(side_effect=list_events)
    app = _make_app(run_store, event_store, user_id=user_a)

    with TestClient(app) as client:
        response = client.get(f"/api/threads/{thread_id}/context-usage")

    assert response.status_code == 200
    data = response.json()
    assert data["latest"]["run_id"] == "run-a"
    assert data["latest"]["estimated_tokens"] == 11
    assert [item["run_id"] for item in data["recent"]] == ["run-a"]
    run_store.list_by_thread.assert_awaited_once_with(thread_id, user_id=str(user_a), limit=20)
    event_store.list_events.assert_awaited_once_with(
        thread_id,
        "run-a",
        event_types=["llm.context"],
        limit=200,
        user_id=str(user_a),
    )
