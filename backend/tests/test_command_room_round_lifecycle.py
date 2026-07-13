from deerflow.command_room.round_lifecycle import build_round_lifecycle_hint


def test_round_lifecycle_reports_status_counts_without_selecting_next_state():
    hint = build_round_lifecycle_hint(
        round_id="round-1",
        pending_handoffs=[{"handoff_id": "h1", "round_id": "round-1", "status": "pending"}],
        planned_lanes=[{"lane_id": "lane-1", "round_id": "round-1", "status": "completed"}],
        task_lanes=[
            {"task_id": "task-1", "round_id": "round-1", "status": "running"},
            {"task_id": "task-2", "round_id": "round-2", "status": "failed"},
        ],
        round_state={"status": "active"},
    )

    assert hint.next_state_hint is None
    assert hint.status_counts["pending_handoffs"] == {"pending": 1}
    assert hint.status_counts["planned_lanes"] == {"completed": 1}
    assert hint.status_counts["task_lanes"] == {"running": 1}
    assert hint.warnings == []
    assert hint.unknowns == []
    assert hint.programmatic_decision is False
    assert hint.auto_dispatch is False
    assert hint.quality_verdict is None
