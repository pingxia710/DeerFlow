from __future__ import annotations

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

_USER_1 = UUID("12345678-1111-1111-1111-123456789abc")
_USER_2 = UUID("12345678-2222-2222-2222-123456789abc")


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


def _patch_paths(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("deerflow.command_room.role_state.get_paths", lambda: _Paths(tmp_path))
    monkeypatch.setattr("deerflow.command_room.pending_handoff.get_paths", lambda: _Paths(tmp_path))
    monkeypatch.setattr("deerflow.command_room.plan.get_paths", lambda: _Paths(tmp_path))


def _app(user_id: UUID):
    app = make_authed_test_app(user_factory=lambda: _user(user_id))
    app.include_router(thread_runs.router)
    app.state.run_manager = SimpleNamespace(get=AsyncMock(return_value=_run_record()))
    return app


def test_role_state_api_creates_lists_and_stays_advisory(tmp_path, monkeypatch) -> None:
    _patch_paths(tmp_path, monkeypatch)
    app = _app(_USER_1)

    with TestClient(app) as client:
        created = client.post(
            "/api/threads/thread-1/runs/run-1/role-states",
            json={
                "role_name": "Evidence",
                "summary": "Evidence role keeps one compact accepted memory item.",
                "current_focus": "Check concrete runtime refs.",
                "open_questions": ["Which reload path still needs evidence?"],
                "accepted_signals": ["Worker self-claims remain weak."],
                "evidence_refs": ["command: pytest tests/test_run_operating_plane_api.py -q; exit code: 0"],
            },
        )
        listed = client.get("/api/threads/thread-1/runs/run-1/role-states?role_name=evidence")

    assert created.status_code == 200
    created_body = created.json()
    assert created_body["ai_authored"] is True
    assert created_body["programmatic_decision"] is False
    assert created_body["auto_dispatch"] is False
    assert created_body["role_name"] == "evidence"
    assert created_body["run_id"] == "run-1"
    assert created_body["round_id"] == "round-1"

    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["state_id"] == created_body["state_id"]
    assert "auto_rework" not in rows[0]


def test_pending_handoff_api_lists_and_resolves_without_dispatch(tmp_path, monkeypatch) -> None:
    _patch_paths(tmp_path, monkeypatch)
    user_id = str(_USER_1)
    handoff = build_pending_handoff(
        thread_id="thread-1",
        run_id="run-1",
        round_id="round-1",
        task_id="task-1",
        envelope=HandoffEnvelope(
            source_role="planner",
            target_role="Boundary",
            task_or_question="Check bottom-boundary risk.",
            evidence_refs=["command: pytest tests/test_run_operating_plane_api.py -q; exit code: 0"],
            evidence_strength="Strong",
            raw_input_sha256="abc123",
        ),
    )
    record_pending_handoff(handoff, user_id=user_id)
    app = _app(_USER_1)

    with TestClient(app) as client:
        listed = client.get("/api/threads/thread-1/runs/run-1/pending-handoffs")
        resolved = client.post(
            f"/api/threads/thread-1/runs/run-1/pending-handoffs/{handoff.handoff_id}/resolve",
            json={"status": "accepted", "resolution_note": "Chair accepted this handoff for the next round."},
        )
        pending_after = client.get("/api/threads/thread-1/runs/run-1/pending-handoffs")
        accepted = client.get("/api/threads/thread-1/runs/run-1/pending-handoffs?status=accepted")

    assert listed.status_code == 200
    [row] = listed.json()
    assert row["handoff_id"] == handoff.handoff_id
    assert row["target_role"] == "Boundary"
    assert row["status"] == "pending"
    assert row["programmatic_dispatch"] is False
    assert row["auto_dispatch"] is False
    assert "Check bottom-boundary risk." in row["task_or_question"]

    assert resolved.status_code == 200
    assert resolved.json()["status"] == "accepted"
    assert resolved.json()["resolved_by_role"] == "chair"
    assert pending_after.status_code == 200
    assert pending_after.json() == []
    assert accepted.status_code == 200
    assert accepted.json()[0]["handoff_id"] == handoff.handoff_id


def test_operating_plane_api_is_owner_scoped(tmp_path, monkeypatch) -> None:
    _patch_paths(tmp_path, monkeypatch)
    user_1_app = _app(_USER_1)
    user_2_app = _app(_USER_2)

    with TestClient(user_1_app) as client:
        created = client.post(
            "/api/threads/thread-1/runs/run-1/role-states",
            json={"role_name": "planner", "summary": "User one role state."},
        )
        handoff_id = build_pending_handoff(
            thread_id="thread-1",
            run_id="run-1",
            envelope=HandoffEnvelope(source_role="planner", target_role="Evidence", task_or_question="Inspect refs."),
        ).handoff_id

    record_pending_handoff(
        build_pending_handoff(
            thread_id="thread-1",
            run_id="run-1",
            handoff_id=handoff_id,
            envelope=HandoffEnvelope(source_role="planner", target_role="Evidence", task_or_question="Inspect refs."),
        ),
        user_id=str(_USER_1),
    )
    with TestClient(user_2_app) as client:
        states = client.get("/api/threads/thread-1/runs/run-1/role-states")
        handoffs = client.get("/api/threads/thread-1/runs/run-1/pending-handoffs")

    assert created.status_code == 200
    assert states.status_code == 200
    assert states.json() == []
    assert handoffs.status_code == 200
    assert handoffs.json() == []


def test_plan_api_round_plans_lanes_and_chair_decisions_are_advisory(tmp_path, monkeypatch) -> None:
    _patch_paths(tmp_path, monkeypatch)
    app = _app(_USER_1)

    with TestClient(app) as client:
        created_plan = client.post(
            "/api/threads/thread-1/runs/run-1/round-plans",
            json={
                "goal": "Plan the next regression slice.",
                "boundary": "No auto dispatch.",
                "evidence_standard": "pytest evidence",
                "capability_release": ["p1-native-plan"],
                "planned_lanes": [{"target_role": "Evidence", "reason": "Collect refs"}],
            },
        )
        plans = client.get("/api/threads/thread-1/runs/run-1/round-plans")
        latest = client.get("/api/threads/thread-1/runs/run-1/round-plan")

        created_lane = client.post(
            "/api/threads/thread-1/runs/run-1/planned-lanes",
            json={"target_role": "Evidence", "reason": "Run focused tests.", "expected_evidence": "pytest output"},
        )
        lane_id = created_lane.json().get("lane_id")
        update_lane = client.post(
            f"/api/threads/thread-1/runs/run-1/planned-lanes/{lane_id}/status",
            json={"status": "completed", "linked_task_id": "task-1", "evidence_refs": ["pytest: ok"]},
        )
        lanes = client.get("/api/threads/thread-1/runs/run-1/planned-lanes")
        completed_lanes = client.get("/api/threads/thread-1/runs/run-1/planned-lanes?status=completed")
        illegal_lane = client.post(
            "/api/threads/thread-1/runs/run-1/planned-lanes",
            json={"target_role": "Evidence", "reason": "Bad status.", "status": "bogus"},
        )

        created_decision = client.post(
            "/api/threads/thread-1/runs/run-1/chair-decisions",
            json={
                "decision_type": "scope",
                "decision": "Keep native plan records advisory.",
                "reason": "Chair decision must not mutate run verdict/status.",
                "evidence_refs": ["pytest: ok"],
                "affected_lanes": [lane_id],
            },
        )
        decisions = client.get("/api/threads/thread-1/runs/run-1/chair-decisions")
        run_after_decision = client.get("/api/threads/thread-1/runs/run-1")
        quality_after_decision = client.get("/api/threads/thread-1/runs/run-1/quality-context")

    assert created_plan.status_code == 200
    plan_body = created_plan.json()
    assert plan_body["round_id"] == "round-1"
    assert plan_body["auto_dispatch"] is False
    assert plan_body["programmatic_decision"] is False
    assert plans.status_code == 200
    assert plans.json()[0]["plan_id"] == plan_body["plan_id"]
    assert latest.status_code == 200
    assert latest.json()["plan_id"] == plan_body["plan_id"]

    assert created_lane.status_code == 200
    assert created_lane.json()["auto_dispatch"] is False
    assert created_lane.json()["programmatic_decision"] is False
    assert update_lane.status_code == 200
    assert update_lane.json()["status"] == "completed"
    assert update_lane.json()["linked_task_id"] == "task-1"
    assert lanes.status_code == 200
    assert lanes.json()[0]["lane_id"] == lane_id
    assert completed_lanes.status_code == 200
    assert [row["lane_id"] for row in completed_lanes.json()] == [lane_id]
    assert illegal_lane.status_code == 422

    assert created_decision.status_code == 200
    decision_body = created_decision.json()
    assert decision_body["auto_dispatch"] is False
    assert decision_body["programmatic_decision"] is False
    assert decisions.status_code == 200
    assert decisions.json()[0]["decision_id"] == decision_body["decision_id"]
    if run_after_decision.status_code == 200:
        assert run_after_decision.json()["status"] == "success"
    if quality_after_decision.status_code == 200:
        assert quality_after_decision.json().get("quality_verdict") is None


def test_plan_api_is_owner_scoped(tmp_path, monkeypatch) -> None:
    _patch_paths(tmp_path, monkeypatch)
    user_1_app = _app(_USER_1)
    user_2_app = _app(_USER_2)

    with TestClient(user_1_app) as client:
        plan = client.post("/api/threads/thread-1/runs/run-1/round-plans", json={"goal": "User one plan."})
        lane = client.post("/api/threads/thread-1/runs/run-1/planned-lanes", json={"target_role": "Evidence", "reason": "User one lane."})
        decision = client.post(
            "/api/threads/thread-1/runs/run-1/chair-decisions",
            json={"decision_type": "scope", "decision": "User one decision.", "reason": "Owner scoped."},
        )

    with TestClient(user_2_app) as client:
        plans = client.get("/api/threads/thread-1/runs/run-1/round-plans")
        lanes = client.get("/api/threads/thread-1/runs/run-1/planned-lanes")
        decisions = client.get("/api/threads/thread-1/runs/run-1/chair-decisions")
        write_plan = client.post("/api/threads/thread-1/runs/run-1/round-plans", json={"goal": "User two plan."})

    assert plan.status_code == 200
    assert lane.status_code == 200
    assert decision.status_code == 200
    assert plans.status_code == 200
    assert plans.json() == []
    assert lanes.status_code == 200
    assert lanes.json() == []
    assert decisions.status_code == 200
    assert decisions.json() == []
    assert write_plan.status_code == 200
    assert write_plan.json()["plan_id"] != plan.json()["plan_id"]
