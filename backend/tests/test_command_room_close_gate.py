from deerflow.command_room.close_gate import build_close_gate_report


def test_close_gate_reports_rows_and_counts_without_recommendation():
    report = build_close_gate_report(
        thread_id="thread-1",
        run_id="run-1",
        round_id="round-1",
        pending_handoffs=[{"handoff_id": "h1", "round_id": "round-1", "status": "pending"}],
        planned_lanes=[{"lane_id": "lane-1", "round_id": "round-1", "status": "running"}],
        task_lanes=[{"task_id": "task-1", "round_id": "round-1", "status": "failed"}],
        review_invocations=[{"invocation_id": "review-1", "round_id": "round-1", "status": "requested"}],
        quality_signals=[{"signal_id": "quality-1", "round_id": "round-1", "recommendation": "needs_more_evidence"}],
        evidence_refs=[{"ref_id": "ev-1", "ref": "tests passed"}],
    )

    assert report.status_counts["pending_handoffs"] == {"pending": 1}
    assert report.status_counts["planned_lanes"] == {"running": 1}
    assert report.status_counts["task_lanes"] == {"failed": 1}
    assert report.evidence_summary == {"total": 1}
    assert report.quality_signal_summary == {"total": 1}
    assert report.quality_recommendations == {}
    assert report.warnings == []
    assert report.unknowns == []
    assert report.next_check_hint is None
    assert report.round_lifecycle_hint["next_state_hint"] is None
    assert report.programmatic_decision is False
    assert report.auto_dispatch is False
    assert report.quality_verdict is None


def test_close_gate_scopes_rows_to_round_and_never_judges_evidence():
    report = build_close_gate_report(
        thread_id="thread-1",
        run_id="run-1",
        round_id="round-1",
        evidence_refs=[
            {"ref_id": "ev-strong", "strength": "Strong"},
            {"ref_id": "ev-weak", "strength": "Weak"},
        ],
        task_lanes=[
            {"task_id": "task-1", "round_id": "round-1", "status": "completed"},
            {"task_id": "task-2", "round_id": "round-2", "status": "running"},
        ],
    )

    assert report.status_counts["task_lanes"] == {"completed": 1}
    assert report.evidence_summary == {"total": 2}
    assert set(report.evidence_summary) == {"total"}
