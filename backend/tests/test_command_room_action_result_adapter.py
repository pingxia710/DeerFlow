from deerflow.command_room.action_result_adapter import action_result_from_value
from deerflow.command_room.round import Round, RoundItemStatus


def test_dict_with_evidence_can_enter_round_and_complete():
    result = action_result_from_value(
        {
            "action_id": "run-tests",
            "description": "运行测试",
            "status": "success",
            "summary": "测试通过",
            "evidence_refs": ["pytest tests/test_command_room_round.py passed"],
        }
    )

    round_ = Round(goal="验证适配层").record_action_result(result)

    assert result.status == RoundItemStatus.COMPLETED
    assert round_.evidence_refs == ["pytest tests/test_command_room_round.py passed"]
    assert round_.is_complete is True


def test_string_result_does_not_invent_evidence():
    result = action_result_from_value("普通 subagent 输出", default_action_id="subagent-1")
    round_ = Round(goal="验证字符串保守处理").record_action_result(result)

    assert result.action_id == "subagent-1"
    assert result.summary == "普通 subagent 输出"
    assert result.evidence_refs == []
    assert round_.has_evidence is False
    assert round_.is_complete is False


def test_error_input_blocks_completion_as_unresolved_error():
    result = action_result_from_value(RuntimeError("boom"), default_action_id="task-err")
    round_ = Round(goal="验证错误处理").record_action_result(result)

    assert result.status == RoundItemStatus.FAILED
    assert result.error == "boom"
    assert round_.unresolved == ["boom"]
    assert round_.is_complete is False


def test_mapping_error_status_blocks_completion_even_with_evidence():
    result = action_result_from_value(
        {
            "action_id": "task-err",
            "status": "error",
            "evidence_refs": ["log/error.txt"],
            "error": "任务失败",
        }
    )
    round_ = Round(goal="验证错误状态").record_action_result(result)

    assert result.status == RoundItemStatus.FAILED
    assert round_.has_evidence is True
    assert round_.has_open_questions is True
    assert round_.is_complete is False


def test_output_ref_is_not_evidence():
    result = action_result_from_value(
        {
            "action_id": "generate-report",
            "status": "completed",
            "summary": "报告已生成",
            "output_ref": "outputs/report.md",
        }
    )
    round_ = Round(goal="验证输出引用").record_action_result(result)

    assert result.output_ref == "outputs/report.md"
    assert result.evidence_refs == []
    assert round_.evidence_refs == []
    assert round_.is_complete is False


def test_status_aliases_include_running_pending_blocked_failed():
    assert action_result_from_value({"status": "running"}).status == RoundItemStatus.RUNNING
    assert action_result_from_value({"status": "pending"}).status == RoundItemStatus.PENDING
    assert action_result_from_value({"status": "blocked"}).status == RoundItemStatus.BLOCKED
    assert action_result_from_value({"status": "failed"}).status == RoundItemStatus.FAILED
    assert action_result_from_value({"status": "cancelled"}).status == RoundItemStatus.CANCELLED
    assert action_result_from_value({"status": "timed_out"}).status == RoundItemStatus.TIMED_OUT


def test_terminal_reason_distinguishes_cancelled_timeout_and_boundary_blocked():
    cancelled = action_result_from_value({"status": "cancelled"})
    timed_out = action_result_from_value({"status": "timed_out"})
    blocked = action_result_from_value({"status": "blocked"})

    assert cancelled.terminal_reason == "user_cancelled"
    assert timed_out.terminal_reason == "timed_out"
    assert blocked.terminal_reason == "boundary_blocked"


def test_unknown_explicit_status_fails_safe_and_preserves_risk_context():
    result = action_result_from_value(
        {
            "action_id": "subagent-x",
            "status": "mystery",
            "summary": "无法判定",
            "risks": ["needs review"],
            "unresolved": ["missing terminal proof"],
        }
    )

    assert result.status == RoundItemStatus.FAILED
    assert result.terminal_reason == "unknown_status"
    assert result.risks == ["needs review"]
    assert "missing terminal proof" in result.open_questions
    assert "Unknown action_result status: mystery" in result.open_questions
