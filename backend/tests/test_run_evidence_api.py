from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers import thread_runs
from deerflow.runtime import RunRecord, RunStatus

_USER_ID = UUID("66666666-6666-6666-6666-666666666666")


def _user() -> User:
    return User(id=_USER_ID, email="evidence@example.com", password_hash="x", system_role="user")


def _run_record() -> RunRecord:
    return RunRecord(
        run_id="run-1",
        thread_id="thread-1",
        assistant_id=None,
        status=RunStatus.success,
        on_disconnect="cancel",
        round_id="round-1",
    )


def _app(events: list[dict]):
    app = make_authed_test_app(user_factory=_user)
    app.include_router(thread_runs.router)
    app.state.run_manager = SimpleNamespace(get=AsyncMock(return_value=_run_record()))
    app.state.run_event_store = SimpleNamespace(list_events=AsyncMock(return_value=events))
    return app


def test_run_evidence_api_extracts_normalized_refs_without_quality_verdict() -> None:
    events = [
        {
            "thread_id": "thread-1",
            "run_id": "run-1",
            "event_type": "task_completed",
            "category": "message",
            "seq": 1,
            "created_at": "2026-01-01T00:00:00+00:00",
            "content": {
                "task_id": "task-1",
                "action_result": {
                    "summary": "pytest completed",
                    "evidence_refs": [
                        "command: python -m pytest tests/test_command_room_evidence.py; exit code: 0; stdout: 6 passed",
                        "tests passed",
                    ],
                    "output_ref": "worker-output-123",
                },
                "artifact_refs": ["/mnt/user-data/outputs/report.md"],
            },
            "metadata": {"caller": "task_event", "task_id": "task-1"},
        },
        {
            "thread_id": "thread-1",
            "run_id": "run-1",
            "event_type": "artifact.presented",
            "category": "artifact",
            "seq": 2,
            "created_at": "2026-01-01T00:00:01+00:00",
            "content": {"artifact_refs": ["/mnt/user-data/outputs/report.md"]},
            "metadata": {"caller": "lead_agent", "source_tool": "present_files"},
        },
        {
            "thread_id": "thread-1",
            "run_id": "run-1",
            "event_type": "llm.tool.result",
            "category": "message",
            "seq": 3,
            "created_at": "2026-01-01T00:00:02+00:00",
            "content": {
                "type": "tool",
                "name": "bash",
                "tool_call_id": "tc-1",
                "content": "line 1\napi_key=sk-123456789012345\n<think>hidden reasoning</think>" + ("x" * 900),
            },
            "metadata": {},
        },
        {
            "thread_id": "thread-1",
            "run_id": "run-1",
            "event_type": "run.error",
            "category": "error",
            "seq": 4,
            "created_at": "2026-01-01T00:00:03+00:00",
            "content": "Traceback\nsecret=abc123\nRuntimeError: failed",
            "metadata": {"caller": "runtime"},
        },
    ]
    app = _app(events)

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/runs/run-1/evidence")

    assert response.status_code == 200
    body = response.json()
    refs = body["evidence_refs"]
    by_kind = {ref["source_kind"]: ref for ref in refs}

    assert body["thread_id"] == "thread-1"
    assert body["run_id"] == "run-1"
    assert body["round_id"] == "round-1"
    assert {ref["source_kind"] for ref in refs} == {
        "unknown",
        "output_ref",
        "artifact",
        "command_output",
        "log",
    }
    assert all(ref["strength"] is None for ref in refs)
    assert body["summary"]["quality_verdict"] is None
    assert body["summary"]["auto_rework"] is False
    assert "by_strength" not in body["summary"]

    public_json = json.dumps(body)
    assert "sk-123456789012345" not in public_json
    assert "hidden reasoning" not in public_json
    assert "secret=abc123" not in public_json
    assert len(by_kind["command_output"]["excerpt"]) <= 500
    app.state.run_event_store.list_events.assert_awaited_once_with(
        "thread-1",
        "run-1",
        event_types=[
            "artifact.presented",
            "llm.tool.result",
            "run.error",
            "llm.error",
            "task_completed",
            "task_failed",
            "task_cancelled",
            "task_timed_out",
            "task.completed",
            "task.failed",
            "task.cancelled",
            "task.timed_out",
        ],
        limit=500,
        user_id=str(_USER_ID),
    )


def test_run_evidence_api_404s_for_thread_mismatch_without_reading_events() -> None:
    app = make_authed_test_app(user_factory=_user)
    app.include_router(thread_runs.router)
    app.state.run_manager = SimpleNamespace(
        get=AsyncMock(
            return_value=RunRecord(
                run_id="run-1",
                thread_id="other-thread",
                assistant_id=None,
                status=RunStatus.success,
                on_disconnect="cancel",
            )
        )
    )
    app.state.run_event_store = SimpleNamespace(list_events=AsyncMock(return_value=[]))

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/runs/run-1/evidence")

    assert response.status_code == 404
    app.state.run_event_store.list_events.assert_not_called()
