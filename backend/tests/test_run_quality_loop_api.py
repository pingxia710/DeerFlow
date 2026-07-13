from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers import thread_runs
from deerflow.runtime import RunRecord, RunStatus

_USER_ID = UUID("77777777-7777-7777-7777-777777777777")


def _user() -> User:
    return User(id=_USER_ID, email="quality@example.com", password_hash="x", system_role="user")


def _run_record() -> RunRecord:
    return RunRecord(
        run_id="run-1",
        thread_id="thread-1",
        assistant_id="command-room",
        status=RunStatus.success,
        on_disconnect="cancel",
        round_id="round-1",
        metadata={"round_context": {"round_id": "round-1", "state": "closed", "current_run_id": "run-1"}},
    )


class _Paths:
    def __init__(self, root: Path) -> None:
        self.root = root

    def thread_dir(self, thread_id: str, *, user_id: str | None = None) -> Path:
        return self.root / str(user_id or "legacy") / thread_id


class _RoundStore:
    async def list_by_thread(self, thread_id: str, *, user_id: str | None = None, limit: int = 50):
        return [
            {
                "round_id": "round-1",
                "thread_id": thread_id,
                "user_id": user_id,
                "current_run_id": "run-1",
                "state": "closed",
                "evidence_refs": [],
                "artifact_refs": [],
            }
        ]

    async def list_task_lanes_by_round(self, *, thread_id: str, round_id: str, user_id: str | None = None, limit: int = 100):
        return [
            {
                "thread_id": thread_id,
                "run_id": "run-1",
                "task_id": "task-1",
                "round_id": round_id,
                "user_id": user_id,
                "role": "evidence",
                "status": "completed",
                "handoff": {"targetRole": "Chair", "taskOrQuestion": "inspect evidence refs", "evidenceStrength": "Unverified"},
            }
        ]


def _app(tmp_path, monkeypatch):
    monkeypatch.setattr("deerflow.command_room.quality.get_paths", lambda: _Paths(tmp_path))
    app = make_authed_test_app(user_factory=_user)
    app.include_router(thread_runs.router)
    app.state.run_manager = SimpleNamespace(
        get=AsyncMock(return_value=_run_record()),
        begin_thread_write=AsyncMock(),
        end_thread_write=AsyncMock(),
    )
    app.state.run_event_store = SimpleNamespace(list_events=AsyncMock(return_value=[]))
    app.state.round_state_store = _RoundStore()
    return app


def test_quality_signal_api_saves_and_quality_context_reads_it(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        created = client.post(
            "/api/threads/thread-1/runs/run-1/quality-signals",
            json={
                "task_id": "task-1",
                "author_role": "evidence",
                "recommendation": "needs_more_evidence",
                "rationale": "Worker self-claim is useful but still needs a command output ref.",
                "evidence_refs": ["worker says tests passed"],
                "target_role": "Chair",
            },
        )
        context = client.get("/api/threads/thread-1/runs/run-1/quality-context")

    assert created.status_code == 200
    created_body = created.json()
    assert created_body["ai_authored"] is True
    assert created_body["programmatic_decision"] is False
    assert created_body["quality_verdict"] is None
    assert created_body["auto_rework"] is False
    assert created_body["recommendation"] == "needs_more_evidence"
    assert created_body["evidence_refs"] == ["worker says tests passed"]
    assert created_body["capability_snapshot_version"] == 2

    assert context.status_code == 200
    body = context.json()
    assert body["round_state"]["state"] == "closed"
    assert body["handoffs"][0]["handoff"]["targetRole"] == "Chair"
    assert body["evidence"]["summary"]["quality_verdict"] is None
    assert body["evidence"]["summary"]["auto_rework"] is False
    assert body["quality_signals"][0]["signal_id"] == created_body["signal_id"]
    assert body["quality_signal_summary"]["by_recommendation"] == {"needs_more_evidence": 1}
    assert body["quality_verdict"] is None
    assert body["auto_rework"] is False


def test_quality_signal_api_rejects_pass_fail_recommendation(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        response = client.post(
            "/api/threads/thread-1/runs/run-1/quality-signals",
            json={
                "author_role": "chair",
                "recommendation": "pass",
                "rationale": "not allowed",
            },
        )

    assert response.status_code == 422
