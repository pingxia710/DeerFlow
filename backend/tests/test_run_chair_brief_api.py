from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers import thread_runs
from deerflow.command_room.account_ledger import build_account_decision, build_account_update_proposal, record_account_decision, record_account_update_proposal
from deerflow.command_room.quality import build_quality_signal, record_quality_signal
from deerflow.command_room.review import build_review_invocation, complete_review_invocation, record_review_invocation
from deerflow.runtime import RunRecord, RunStatus

_USER_1 = UUID("55555555-5555-5555-5555-555555555555")
_USER_2 = UUID("99999999-9999-9999-9999-999999999999")


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


class _RoundStore:
    async def list_by_thread(self, thread_id: str, *, user_id: str | None = None, limit: int = 50):
        return [{"round_id": "round-1", "thread_id": thread_id, "user_id": user_id, "current_run_id": "run-1", "state": "closed"}]

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
                "handoff": {
                    "targetRole": "Chair",
                    "taskOrQuestion": "inspect evidence refs",
                    "evidenceStrength": "Strong",
                    "evidenceRefs": ["command: pytest -q; exit code: 0"],
                },
            }
        ]


def _patch_paths(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("deerflow.command_room.quality.get_paths", lambda: _Paths(tmp_path))
    monkeypatch.setattr("deerflow.command_room.review.get_paths", lambda: _Paths(tmp_path))
    monkeypatch.setattr("deerflow.command_room.account_ledger.get_paths", lambda: _Paths(tmp_path))


def _app(user_id: UUID, events: list[dict]):
    app = make_authed_test_app(user_factory=lambda: _user(user_id))
    app.include_router(thread_runs.router)
    app.state.run_manager = SimpleNamespace(get=AsyncMock(return_value=_run_record()))
    app.state.run_event_store = SimpleNamespace(list_events=AsyncMock(return_value=events))
    app.state.round_state_store = _RoundStore()
    return app


def test_chair_brief_api_returns_owner_scoped_compact_read_model(tmp_path, monkeypatch) -> None:
    _patch_paths(tmp_path, monkeypatch)
    user_id = str(_USER_1)
    signal = build_quality_signal(
        thread_id="thread-1",
        run_id="run-1",
        round_id="round-1",
        task_id="task-1",
        author_role="evidence",
        recommendation="needs_more_evidence",
        rationale="Need one concrete command ref.",
        evidence_refs=["command: pytest -q; exit code: 0"],
    )
    invocation = build_review_invocation(
        thread_id="thread-1",
        run_id="run-1",
        round_id="round-1",
        task_id="task-1",
        requested_by_role="lead",
        reviewer_role="opposition",
        reason="Check the evidence boundary.",
        focus="Inspect the command output ref.",
    )
    proposal = build_account_update_proposal(
        thread_id="thread-1",
        run_id="run-1",
        round_id="round-1",
        task_id="task-1",
        proposed_by_role="planner",
        account_type="goal",
        proposed_change="Keep the goal scoped to this round.",
        rationale="The handoff narrowed the request.",
    )
    decision = build_account_decision(proposal_id=proposal.proposal_id, thread_id="thread-1", run_id="run-1", decision="defer", rationale="Chair keeps it advisory for now.")
    record_quality_signal(signal, user_id=user_id)
    record_review_invocation(complete_review_invocation(invocation, result_summary="Evidence boundary is clear.", result_evidence_refs=[]), user_id=user_id)
    record_account_update_proposal(proposal, user_id=user_id)
    record_account_decision(decision, user_id=user_id)
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
                "round_id": "round-1",
                "action_result": {
                    "summary": "pytest completed",
                    "evidence_refs": ["command: pytest tests/test_run_chair_brief_api.py -q; exit code: 0; stdout: ok"],
                },
            },
            "metadata": {"caller": "task_event", "task_id": "task-1"},
        }
    ]
    app = _app(_USER_1, events)

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/runs/run-1/chair-brief?round_id=round-1&task_id=task-1")

    assert response.status_code == 200
    body = response.json()
    assert body["thread_id"] == "thread-1"
    assert body["run_id"] == "run-1"
    assert body["round_id"] == "round-1"
    assert body["task_id"] == "task-1"
    assert body["capability_snapshot_version"] == 2
    assert body["handoff_count"] == 1
    assert body["latest_handoff"]["target_role"] == "Chair"
    assert body["evidence_summary"]["total"] == 1
    assert body["quality_signals"][0]["recommendation"] == "needs_more_evidence"
    assert "quality_verdict" not in body["quality_signals"][0]
    assert "auto_rework" not in body["quality_signals"][0]
    assert body["review_invocations"][0]["status"] == "completed"
    assert body["account_proposals"][0]["proposal_id"] == proposal.proposal_id
    assert body["account_decisions"][0]["decision"] == "defer"
    assert body["source_counts"] == {
        "handoffs": 1,
        "evidence_refs": 1,
        "quality_signals": 1,
        "review_invocations": 1,
        "account_proposals": 1,
        "account_decisions": 1,
    }
    assert body["known_gaps"] == []
    public_json = json.dumps(body).lower()
    assert "pass" not in public_json
    assert "fail" not in public_json
    assert "needs_rework" not in public_json


def test_chair_brief_api_reads_audit_records_from_request_owner_only(tmp_path, monkeypatch) -> None:
    _patch_paths(tmp_path, monkeypatch)
    record_quality_signal(
        build_quality_signal(
            thread_id="thread-1",
            run_id="run-1",
            author_role="evidence",
            recommendation="needs_more_evidence",
            rationale="Owned by another user.",
        ),
        user_id=str(_USER_1),
    )
    app = _app(_USER_2, [])

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/runs/run-1/chair-brief")

    assert response.status_code == 200
    body = response.json()
    assert body["quality_signals"] == []
    assert body["source_counts"]["quality_signals"] == 0
    assert body["known_gaps"] == []
