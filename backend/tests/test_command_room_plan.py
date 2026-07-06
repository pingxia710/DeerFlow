from __future__ import annotations

import pytest

from deerflow.command_room.plan import (
    build_chair_decision,
    build_planned_lane,
    build_round_plan,
    find_planned_lane_by_linked_task_id,
    latest_round_plan,
    list_chair_decisions,
    list_planned_lanes,
    list_round_plans,
    record_chair_decision,
    record_planned_lane,
    record_round_plan,
    update_planned_lane_status,
    update_planned_lane_status_by_linked_task_id,
)


def _assert_advisory(row: dict) -> None:
    assert row["auto_dispatch"] is False
    assert row["programmatic_decision"] is False


def test_round_plan_build_record_list_latest_is_advisory(tmp_path) -> None:
    plan1 = build_round_plan(thread_id="thread-1", run_id="run-1", round_id="round-1", goal="Goal one")
    plan2 = build_round_plan(thread_id="thread-1", run_id="run-1", round_id="round-1", goal="Goal two")

    _assert_advisory(plan1.as_dict())
    record_round_plan(plan1, user_id="user-1", base_dir=tmp_path)
    record_round_plan(plan2, user_id="user-1", base_dir=tmp_path)

    rows = list_round_plans(thread_id="thread-1", user_id="user-1", run_id="run-1", round_id="round-1", base_dir=tmp_path)
    assert [row["plan_id"] for row in rows] == [plan1.plan_id, plan2.plan_id]
    assert latest_round_plan(thread_id="thread-1", user_id="user-1", run_id="run-1", round_id="round-1", base_dir=tmp_path)["plan_id"] == plan2.plan_id
    assert all(row["auto_dispatch"] is False and row["programmatic_decision"] is False for row in rows)


def test_planned_lane_build_record_list_update_status_and_filter_is_advisory(tmp_path) -> None:
    lane1 = build_planned_lane(thread_id="thread-1", run_id="run-1", round_id="round-1", target_role="Evidence", reason="Find proof")
    lane2 = build_planned_lane(thread_id="thread-1", run_id="run-1", round_id="round-1", target_role="Boundary", reason="Check boundary", status="blocked")
    _assert_advisory(lane1.as_dict())

    record_planned_lane(lane1, user_id="user-1", base_dir=tmp_path)
    record_planned_lane(lane2, user_id="user-1", base_dir=tmp_path)
    updated = update_planned_lane_status(
        thread_id="thread-1",
        user_id="user-1",
        run_id="run-1",
        lane_id=lane1.lane_id,
        status="completed",
        linked_task_id="task-1",
        evidence_refs=["ref-1"],
        base_dir=tmp_path,
    )

    assert updated is not None
    assert updated.status == "completed"
    assert updated.linked_task_id == "task-1"
    assert updated.evidence_refs == ["ref-1"]
    rows = list_planned_lanes(thread_id="thread-1", user_id="user-1", run_id="run-1", base_dir=tmp_path)
    assert {row["lane_id"]: row["status"] for row in rows} == {lane1.lane_id: "completed", lane2.lane_id: "blocked"}
    completed = list_planned_lanes(thread_id="thread-1", user_id="user-1", run_id="run-1", status="completed", base_dir=tmp_path)
    assert [row["lane_id"] for row in completed] == [lane1.lane_id]
    assert all(row["auto_dispatch"] is False and row["programmatic_decision"] is False for row in rows)


def test_update_planned_lane_status_by_linked_task_id_updates_latest_matching_lane(tmp_path) -> None:
    older = build_planned_lane(
        thread_id="thread-1",
        run_id="run-1",
        round_id="round-1",
        target_role="Evidence",
        reason="Older linked lane",
        linked_task_id="task-1",
        evidence_refs=["existing-ref"],
        artifact_refs=["existing-artifact"],
    )
    newer = build_planned_lane(
        thread_id="thread-1",
        run_id="run-1",
        round_id="round-1",
        target_role="Boundary",
        reason="Newer linked lane",
        linked_task_id="task-1",
        output_refs=["existing-output"],
    )
    other = build_planned_lane(
        thread_id="thread-1",
        run_id="run-1",
        round_id="round-1",
        target_role="General",
        reason="Different linked lane",
        linked_task_id="task-2",
    )
    record_planned_lane(older, user_id="user-1", base_dir=tmp_path)
    record_planned_lane(newer, user_id="user-1", base_dir=tmp_path)
    record_planned_lane(other, user_id="user-1", base_dir=tmp_path)

    found = find_planned_lane_by_linked_task_id(thread_id="thread-1", user_id="user-1", run_id="run-1", linked_task_id="task-1", base_dir=tmp_path)
    assert found is not None
    assert found["lane_id"] == newer.lane_id

    updated = update_planned_lane_status_by_linked_task_id(
        thread_id="thread-1",
        user_id="user-1",
        run_id="run-1",
        round_id="round-1",
        linked_task_id="task-1",
        status="completed",
        evidence_refs=["new-ref", "new-ref"],
        artifact_refs=["new-artifact"],
        output_refs=["new-output"],
        base_dir=tmp_path,
    )

    assert updated is not None
    assert updated.lane_id == newer.lane_id
    assert updated.status == "completed"
    assert updated.linked_task_id == "task-1"
    assert updated.evidence_refs == ["new-ref"]
    assert updated.artifact_refs == ["new-artifact"]
    assert updated.output_refs == ["existing-output", "new-output"]
    _assert_advisory(updated.as_dict())
    rows = list_planned_lanes(thread_id="thread-1", user_id="user-1", run_id="run-1", base_dir=tmp_path)
    assert {row["lane_id"]: row["status"] for row in rows} == {older.lane_id: "planned", newer.lane_id: "completed", other.lane_id: "planned"}
    assert all(row["auto_dispatch"] is False and row["programmatic_decision"] is False for row in rows)


def test_update_planned_lane_status_by_linked_task_id_missing_returns_none_without_creating(tmp_path) -> None:
    lane = build_planned_lane(thread_id="thread-1", run_id="run-1", target_role="Evidence", reason="Find proof", linked_task_id="task-1")
    record_planned_lane(lane, user_id="user-1", base_dir=tmp_path)

    updated = update_planned_lane_status_by_linked_task_id(
        thread_id="thread-1",
        user_id="user-1",
        run_id="run-1",
        linked_task_id="missing-task",
        status="completed",
        evidence_refs=["ref-1"],
        base_dir=tmp_path,
    )

    assert updated is None
    assert find_planned_lane_by_linked_task_id(thread_id="thread-1", user_id="user-1", run_id="run-1", linked_task_id="missing-task", base_dir=tmp_path) is None
    rows = list_planned_lanes(thread_id="thread-1", user_id="user-1", run_id="run-1", base_dir=tmp_path)
    assert len(rows) == 1
    assert rows[0]["lane_id"] == lane.lane_id
    assert rows[0]["status"] == "planned"
    assert rows[0]["evidence_refs"] == []
    _assert_advisory(rows[0])


def test_illegal_planned_lane_status_raises_value_error(tmp_path) -> None:
    with pytest.raises(ValueError):
        build_planned_lane(thread_id="thread-1", run_id="run-1", target_role="Evidence", reason="Find proof", status="bogus")

    lane = build_planned_lane(thread_id="thread-1", run_id="run-1", target_role="Evidence", reason="Find proof")
    record_planned_lane(lane, user_id="user-1", base_dir=tmp_path)
    with pytest.raises(ValueError):
        update_planned_lane_status(thread_id="thread-1", user_id="user-1", run_id="run-1", lane_id=lane.lane_id, status="bogus", base_dir=tmp_path)


def test_chair_decision_build_record_list_is_advisory(tmp_path) -> None:
    decision = build_chair_decision(
        thread_id="thread-1",
        run_id="run-1",
        round_id="round-1",
        decision_type="scope",
        decision="Keep this advisory.",
        reason="No auto dispatch.",
        evidence_refs=["ref-1"],
        affected_lanes=["lane-1"],
        handoffs=["handoff-1"],
    )
    _assert_advisory(decision.as_dict())
    record_chair_decision(decision, user_id="user-1", base_dir=tmp_path)

    rows = list_chair_decisions(thread_id="thread-1", user_id="user-1", run_id="run-1", round_id="round-1", base_dir=tmp_path)
    assert len(rows) == 1
    assert rows[0]["decision_id"] == decision.decision_id
    assert rows[0]["evidence_refs"] == ["ref-1"]
    assert rows[0]["auto_dispatch"] is False
    assert rows[0]["programmatic_decision"] is False
