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

_USER_1 = UUID("11111111-1111-1111-1111-111111111111")
_USER_2 = UUID("22222222-2222-2222-2222-222222222222")


def _user(user_id: UUID) -> User:
    return User(id=user_id, email=f"{user_id}@example.com", password_hash="x", system_role="user")


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


def _app(tmp_path, monkeypatch, user_id: UUID):
    monkeypatch.setattr("deerflow.command_room.review.get_paths", lambda: _Paths(tmp_path))
    app = make_authed_test_app(user_factory=lambda: _user(user_id))
    app.include_router(thread_runs.router)
    app.state.run_manager = SimpleNamespace(
        get=AsyncMock(return_value=_run_record()),
        begin_thread_write=AsyncMock(),
        end_thread_write=AsyncMock(),
    )
    return app


def test_review_invocation_api_creates_lists_and_completes(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch, _USER_1)

    with TestClient(app) as client:
        created = client.post(
            "/api/threads/thread-1/runs/run-1/review-invocations",
            json={
                "task_id": "task-1",
                "requested_by_role": "lead",
                "reviewer_role": "opposition",
                "reason": "Need adversarial review of a weak evidence claim.",
                "focus": "Check whether the claim is supported by concrete refs.",
                "evidence_refs": ["summary only"],
                "handoff_refs": ["handoff:task-1"],
                "quality_signal_refs": ["quality-1"],
            },
        )
        invocation_id = created.json()["invocation_id"]
        completed = client.post(
            f"/api/threads/thread-1/runs/run-1/review-invocations/{invocation_id}/complete",
            json={
                "result_summary": "The claim needs a command output ref before Chair acts.",
                "result_evidence_refs": ["findings.md"],
            },
        )
        listed = client.get("/api/threads/thread-1/runs/run-1/review-invocations")

    assert created.status_code == 200
    assert created.json()["ai_authored"] is True
    assert created.json()["status"] == "requested"
    assert created.json()["target_role"] == "Chair"
    assert "auto_rework" not in created.json()

    assert completed.status_code == 200
    completed_body = completed.json()
    assert completed_body["invocation_id"] == invocation_id
    assert completed_body["status"] == "completed"
    assert completed_body["result_evidence_refs"] == ["findings.md"]

    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"


def test_review_invocation_api_rejects_unknown_reviewer_role(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch, _USER_1)

    with TestClient(app) as client:
        response = client.post(
            "/api/threads/thread-1/runs/run-1/review-invocations",
            json={
                "requested_by_role": "chair",
                "reviewer_role": "planner",
                "reason": "Needs review.",
                "focus": "Review this.",
            },
        )

    assert response.status_code == 422


def test_review_invocation_api_is_owner_scoped(tmp_path, monkeypatch) -> None:
    user_1_app = _app(tmp_path, monkeypatch, _USER_1)
    user_2_app = _app(tmp_path, monkeypatch, _USER_2)

    with TestClient(user_1_app) as client:
        created = client.post(
            "/api/threads/thread-1/runs/run-1/review-invocations",
            json={
                "requested_by_role": "lead",
                "reviewer_role": "reviewer",
                "reason": "Need a second look at the synthesis.",
                "focus": "Check the synthesis summary only.",
            },
        )
    with TestClient(user_2_app) as client:
        listed = client.get("/api/threads/thread-1/runs/run-1/review-invocations")

    assert created.status_code == 200
    assert listed.status_code == 200
    assert listed.json() == []
