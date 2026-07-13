from deerflow.command_room.round import RoundItemStatus
from deerflow.command_room.task_action_result import (
    task_action_result_event,
    task_action_result_from_terminal_event,
)


def test_task_terminal_string_result_becomes_summary_not_evidence():
    result = task_action_result_from_terminal_event(
        task_id="task-1",
        status="completed",
        description="check files",
        result="done",
    )

    assert result.action_id == "task-1"
    assert result.description == "check files"
    assert result.status == RoundItemStatus.COMPLETED
    assert result.summary == "done"
    assert result.evidence_refs == []


def test_task_action_result_event_is_structured_metadata():
    result = task_action_result_from_terminal_event(
        task_id="task-2",
        status="failed",
        description="run check",
        error="boom",
        terminal_reason="failed",
    )

    event = task_action_result_event(result)

    assert event["type"] == "task_action_result"
    assert event["action_result"]["action_id"] == "task-2"
    assert event["action_result"]["status"] == "failed"
    assert event["action_result"]["terminal_reason"] == "failed"
    assert event["action_result"]["error"] == "boom"
    assert event["action_result"]["evidence_refs"] == []


def test_task_terminal_cancelled_is_not_boundary_blocked():
    result = task_action_result_from_terminal_event(
        task_id="task-cancel",
        status="cancelled",
        description="cancel check",
        error="user cancelled",
        terminal_reason="user_cancelled",
    )

    assert result.status == RoundItemStatus.CANCELLED
    assert result.terminal_reason == "user_cancelled"


def test_task_terminal_timeout_keeps_timeout_status():
    result = task_action_result_from_terminal_event(
        task_id="task-timeout",
        status="timed_out",
        description="timeout check",
        error="timeout",
        terminal_reason="timed_out",
    )

    assert result.status == RoundItemStatus.TIMED_OUT
    assert result.terminal_reason == "timed_out"


def test_task_terminal_dict_result_does_not_trust_model_claimed_evidence():
    result = task_action_result_from_terminal_event(
        task_id="task-3",
        status="completed",
        description="claimed check",
        result={
            "summary": "I ran everything and it passed",
            "evidence_refs": ["pytest passed"],
        },
    )

    assert result.summary == "I ran everything and it passed"
    assert result.evidence_refs == []
    assert result.risks == []


def test_task_terminal_summary_only_dict_does_not_become_strong_evidence():
    result = task_action_result_from_terminal_event(
        task_id="task-4",
        status="completed",
        result={"summary": "总结：测试通过，已完成"},
    )

    assert result.evidence_refs == []
    assert result.risks == []


def test_task_terminal_dict_drops_self_claimed_verification_fields():
    result = task_action_result_from_terminal_event(
        task_id="task-5",
        status="completed",
        description="worker claims verification",
        result={
            "summary": "verified=true; tests passed",
            "verified": True,
            "evidence_refs": ["tests passed", "verified=true", "worker says done"],
        },
    )

    assert result.summary == "verified=true; tests passed"
    assert result.evidence_refs == []
    assert result.risks == []


def test_task_terminal_runtime_identity_overrides_model_claims():
    result = task_action_result_from_terminal_event(
        task_id="runtime-task",
        status="completed",
        description="runtime description",
        result={
            "action_id": "claimed-task",
            "description": "claimed description",
            "summary": "done",
        },
    )

    assert result.action_id == "runtime-task"
    assert result.description == "runtime description"
