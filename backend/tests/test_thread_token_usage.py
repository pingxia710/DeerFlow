"""Tests for thread-level token usage aggregation API."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.routers import thread_runs


def _make_app(run_store: MagicMock, event_store: MagicMock | None = None):
    app = make_authed_test_app()
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
    run_store.aggregate_tokens_by_thread.assert_awaited_once_with("thread-1")


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
    run_store.aggregate_tokens_by_thread.assert_awaited_once_with("thread-1", include_active=True)


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
    run_store.list_by_thread.assert_awaited_once_with("thread-1", limit=20)
