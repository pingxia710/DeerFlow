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
        result="Task Succeeded. Result: done",
    )

    assert result.action_id == "task-1"
    assert result.description == "check files"
    assert result.status == RoundItemStatus.COMPLETED
    assert result.summary == "Task Succeeded. Result: done"
    assert result.evidence_refs == []


def test_task_action_result_event_is_structured_metadata():
    result = task_action_result_from_terminal_event(
        task_id="task-2",
        status="failed",
        description="run check",
        error="boom",
    )

    event = task_action_result_event(result)

    assert event["type"] == "task_action_result"
    assert event["action_result"]["action_id"] == "task-2"
    assert event["action_result"]["status"] == "failed"
    assert event["action_result"]["error"] == "boom"
    assert event["action_result"]["evidence_refs"] == []


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
    assert "untrusted model-text evidence_refs not promoted" in result.risks


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
    assert "untrusted model-text evidence_refs not promoted" in result.risks
