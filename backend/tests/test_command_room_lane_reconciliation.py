from deerflow.command_room.close_gate import build_close_gate_report
from deerflow.command_room.lane_reconciliation import build_lane_reconciliation_facts


def _types(result):
    return {fact["type"] for fact in result.facts}


def test_reports_planned_lane_linked_task_missing():
    result = build_lane_reconciliation_facts(
        planned_lanes=[{"lane_id": "lane-1", "target_role": "researcher", "linked_task_id": "task-missing"}],
        task_lanes=[],
    )

    assert "planned_lane_linked_task_missing" in _types(result)
    assert result.programmatic_decision is False
    assert result.auto_dispatch is False
    assert result.quality_verdict is None


def test_reports_task_lane_without_linked_planned_lane():
    result = build_lane_reconciliation_facts(
        planned_lanes=[{"lane_id": "lane-1", "target_role": "researcher"}],
        task_lanes=[{"task_id": "task-1", "role": "researcher"}],
    )

    assert "task_lane_without_linked_planned_lane" in _types(result)


def test_reports_planned_task_role_mismatch_without_verdict():
    result = build_lane_reconciliation_facts(
        planned_lanes=[{"lane_id": "lane-1", "target_role": "researcher", "linked_task_id": "task-1"}],
        task_lanes=[{"task_id": "task-1", "role": "coder"}],
    )

    assert "planned_task_role_mismatch" in _types(result)
    assert result.programmatic_decision is False
    assert result.auto_dispatch is False
    assert result.quality_verdict is None


def test_close_gate_report_includes_lane_reconciliation():
    report = build_close_gate_report(
        thread_id="thread-1",
        run_id="run-1",
        round_id="round-1",
        planned_lanes=[{"lane_id": "lane-1", "round_id": "round-1", "target_role": "researcher", "linked_task_id": "task-1"}],
        task_lanes=[{"task_id": "task-1", "round_id": "round-1", "role": "coder"}],
    )

    data = report.as_dict()
    assert "lane_reconciliation" in data
    assert data["lane_reconciliation"]["programmatic_decision"] is False
    assert data["lane_reconciliation"]["auto_dispatch"] is False
    assert data["lane_reconciliation"]["quality_verdict"] is None
    assert data["lane_reconciliation"]["facts"][0]["type"] == "planned_task_role_mismatch"
