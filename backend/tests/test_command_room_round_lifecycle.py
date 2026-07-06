from deerflow.command_room.round_lifecycle import build_round_lifecycle_hint


def test_round_lifecycle_active_task_lane_hints_executing_without_decision():
    hint = build_round_lifecycle_hint(
        round_id="round-1",
        task_lanes=[{"task_id": "task-1", "round_id": "round-1", "status": "running"}],
        round_state={"status": "active"},
    )

    assert hint.next_state_hint == "executing"
    assert hint.programmatic_decision is False
    assert hint.auto_dispatch is False
    assert hint.quality_verdict is None


def test_round_lifecycle_terminal_lanes_without_chair_hints_awaiting_chair_decision():
    hint = build_round_lifecycle_hint(
        round_id="round-1",
        planned_lanes=[{"lane_id": "lane-1", "round_id": "round-1", "status": "completed"}],
        task_lanes=[{"task_id": "task-1", "round_id": "round-1", "status": "failed"}],
        chair_decisions=[],
        round_state={"status": "active"},
    )

    assert hint.next_state_hint == "awaiting_chair_decision"
    assert hint.programmatic_decision is False
    assert hint.auto_dispatch is False
    assert hint.quality_verdict is None


def test_round_lifecycle_pending_handoff_hints_waiting_user_not_dispatch():
    hint = build_round_lifecycle_hint(
        round_id="round-1",
        pending_handoffs=[{"handoff_id": "h1", "round_id": "round-1", "status": "pending"}],
        task_lanes=[{"task_id": "task-1", "round_id": "round-1", "status": "completed"}],
        round_state={"status": "active"},
    )

    assert hint.next_state_hint == "waiting_user"
    assert hint.programmatic_decision is False
    assert hint.auto_dispatch is False
    assert hint.quality_verdict is None
    assert any("do not auto-dispatch" in warning for warning in hint.warnings)
