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

_USER_1 = UUID("33333333-3333-3333-3333-333333333333")
_USER_2 = UUID("44444444-4444-4444-4444-444444444444")


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
    monkeypatch.setattr("deerflow.command_room.account_ledger.get_paths", lambda: _Paths(tmp_path))
    app = make_authed_test_app(user_factory=lambda: _user(user_id))
    app.include_router(thread_runs.router)
    app.state.run_manager = SimpleNamespace(get=AsyncMock(return_value=_run_record()))
    return app


def test_account_ledger_api_creates_lists_and_decides(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch, _USER_1)

    with TestClient(app) as client:
        created = client.post(
            "/api/threads/thread-1/runs/run-1/account-proposals",
            json={
                "task_id": "task-1",
                "proposed_by_role": "planner",
                "account_type": "goal",
                "proposed_change": "Keep the Goal Account scoped to the active thread.",
                "rationale": "The round evidence narrowed the user objective.",
                "evidence_refs": ["source_ref:docs/goal.md"],
                "quality_signal_refs": ["quality-1"],
                "review_invocation_refs": ["review-1"],
            },
        )
        proposal_id = created.json()["proposal_id"]
        decided = client.post(
            "/api/threads/thread-1/runs/run-1/account-decisions",
            json={
                "proposal_id": proposal_id,
                "decision": "adopt",
                "rationale": "The proposed account wording matches the current evidence.",
                "evidence_refs": ["source_ref:docs/goal.md"],
            },
        )
        listed = client.get("/api/threads/thread-1/runs/run-1/account-proposals")

    assert created.status_code == 200
    created_body = created.json()
    assert created_body["ai_authored"] is True
    assert created_body["proposed_by_role"] == "planner"
    assert created_body["target_role"] == "Chair"
    assert "auto_rework" not in created_body
    assert "auto_apply" not in created_body

    assert decided.status_code == 200
    decided_body = decided.json()
    assert decided_body["proposal_id"] == proposal_id
    assert decided_body["decided_by_role"] == "chair"
    assert decided_body["decision"] == "adopt"
    assert "auto_rework" not in decided_body
    assert "auto_apply" not in decided_body

    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["proposal_id"] == proposal_id


def test_account_ledger_api_rejects_invalid_types_and_missing_proposal(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch, _USER_1)

    with TestClient(app) as client:
        invalid_account = client.post(
            "/api/threads/thread-1/runs/run-1/account-proposals",
            json={
                "proposed_by_role": "planner",
                "account_type": "budget",
                "proposed_change": "Update unsupported account.",
                "rationale": "Unsupported account type.",
            },
        )
        invalid_decision = client.post(
            "/api/threads/thread-1/runs/run-1/account-decisions",
            json={
                "proposal_id": "missing",
                "decision": "approve",
                "rationale": "Unsupported decision.",
            },
        )
        missing_proposal = client.post(
            "/api/threads/thread-1/runs/run-1/account-decisions",
            json={
                "proposal_id": "missing",
                "decision": "reject",
                "rationale": "No proposal record exists for this decision.",
            },
        )

    assert invalid_account.status_code == 422
    assert invalid_decision.status_code == 422
    assert missing_proposal.status_code == 404


def test_account_ledger_api_is_owner_scoped(tmp_path, monkeypatch) -> None:
    user_1_app = _app(tmp_path, monkeypatch, _USER_1)
    user_2_app = _app(tmp_path, monkeypatch, _USER_2)

    with TestClient(user_1_app) as client:
        created = client.post(
            "/api/threads/thread-1/runs/run-1/account-proposals",
            json={
                "proposed_by_role": "debt-curator",
                "account_type": "debt",
                "proposed_change": "Track one follow-up debt item.",
                "rationale": "The current round found a deferred cleanup.",
            },
        )
    with TestClient(user_2_app) as client:
        listed = client.get("/api/threads/thread-1/runs/run-1/account-proposals")

    assert created.status_code == 200
    assert listed.status_code == 200
    assert listed.json() == []
