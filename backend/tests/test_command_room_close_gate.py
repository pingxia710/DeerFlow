from deerflow.command_room.close_gate import build_close_gate_report


def test_close_gate_reports_open_items_without_programmatic_verdict():
    report = build_close_gate_report(
        thread_id="thread-1",
        run_id="run-1",
        round_id="round-1",
        pending_handoffs=[{"handoff_id": "h1", "round_id": "round-1", "status": "pending"}],
        planned_lanes=[{"lane_id": "lane-1", "round_id": "round-1", "status": "running"}],
        task_lanes=[{"task_id": "task-1", "round_id": "round-1", "status": "dispatched"}],
        review_invocations=[{"invocation_id": "review-1", "round_id": "round-1", "status": "requested"}],
        quality_signals=[{"signal_id": "quality-1", "round_id": "round-1", "recommendation": "needs_more_evidence"}],
        chair_decisions=[],
        evidence_refs=[{"ref_id": "ev-weak", "strength": "Weak", "ref": "tests passed"}],
    )

    assert report.programmatic_decision is False
    assert report.auto_dispatch is False
    assert report.quality_verdict is None
    assert report.round_lifecycle_hint["next_state_hint"] == "waiting_user"
    assert report.round_lifecycle_hint["programmatic_decision"] is False
    assert len(report.open_pending_handoffs) == 1
    assert len(report.active_planned_lanes) == 1
    assert len(report.active_task_lanes) == 1
    assert len(report.open_review_invocations) == 1
    assert report.quality_recommendations["needs_more_evidence"] == 1
    assert report.evidence_summary["by_strength"]["weak"] == 1
    assert any("needs_more_evidence" in warning for warning in report.warnings)
    assert report.quality_verdict is None
    assert report.programmatic_decision is False


def test_close_gate_strong_evidence_is_not_pass_verdict():
    report = build_close_gate_report(
        thread_id="thread-1",
        run_id="run-1",
        round_id="round-1",
        evidence_refs=[{"ref_id": "ev-strong", "strength": "Strong", "ref": "pytest stdout exit code 0"}],
        round_state={"status": "reviewing"},
    )

    assert report.programmatic_decision is False
    assert report.auto_dispatch is False
    assert report.quality_verdict is None
    assert report.evidence_summary["has_strong_evidence"] is True
    assert report.evidence_summary["by_strength"]["strong"] == 1
    assert report.quality_verdict is None
    assert report.programmatic_decision is False


def test_close_gate_weak_or_no_evidence_is_not_fail_verdict():
    weak_report = build_close_gate_report(
        thread_id="thread-1",
        run_id="run-1",
        round_id="round-1",
        evidence_refs=[{"ref_id": "ev-unverified", "strength": "Unverified", "ref": "looks fine"}],
    )
    no_evidence_report = build_close_gate_report(thread_id="thread-1", run_id="run-1", round_id="round-1")

    for report in (weak_report, no_evidence_report):
        assert report.programmatic_decision is False
        assert report.auto_dispatch is False
        assert report.quality_verdict is None
        assert report.evidence_summary["has_weak_or_unverified_evidence"] is True
        assert report.programmatic_decision is False
        assert report.quality_verdict is None


def test_close_gate_collects_failed_or_blocked_lanes():
    report = build_close_gate_report(
        thread_id="thread-1",
        run_id="run-1",
        round_id="round-1",
        planned_lanes=[{"lane_id": "lane-failed", "round_id": "round-1", "status": "failed"}],
        task_lanes=[{"task_id": "task-blocked", "round_id": "round-1", "status": "blocked"}],
    )

    assert report.programmatic_decision is False
    assert report.auto_dispatch is False
    assert report.quality_verdict is None
    assert len(report.failed_or_blocked_lanes) == 2
    assert report.quality_verdict is None
    assert report.programmatic_decision is False
