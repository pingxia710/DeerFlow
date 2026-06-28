from deerflow.command_room.round import (
    ActionResult,
    NextRound,
    Round,
    RoundAction,
    RoundItemStatus,
    summarize_round,
)


def test_can_create_round():
    round_ = Round(
        goal="验证轮次契约",
        boundaries=["不改运行时", "不触碰密钥"],
        allowed_actions=["读取相关文件", "运行最小测试"],
        evidence_standard=["测试通过", "状态变化可追溯"],
        next_step="补最小测试",
    )

    assert round_.goal == "验证轮次契约"
    assert round_.boundaries == ["不改运行时", "不触碰密钥"]
    assert round_.allowed_actions == ["读取相关文件", "运行最小测试"]
    assert round_.evidence_standard == ["测试通过", "状态变化可追溯"]
    assert round_.is_complete is False


def test_round_records_facts_and_open_questions_not_only_actions():
    round_ = Round(
        goal="修正轮次定义",
        known_facts=["轮次是边界内的状态推进"],
        open_questions=["摘要是否足够短"],
        actions=[RoundAction(title="检查现有契约", status=RoundItemStatus.COMPLETED)],
        state_delta=["补充输入输出字段"],
        verdict="需要继续验证",
    )

    assert round_.known_facts == ["轮次是边界内的状态推进"]
    assert round_.open_questions == ["摘要是否足够短"]
    assert round_.state_delta == ["补充输入输出字段"]
    assert round_.verdict == "需要继续验证"


def test_completed_subtasks_do_not_complete_whole_round():
    round_ = Round(
        goal="实现轮次 MVP",
        actions=[RoundAction(title="写契约", status=RoundItemStatus.COMPLETED)],
    )

    assert round_.actions_completed is True
    assert round_.is_complete is False


def test_missing_evidence_cannot_be_complete():
    round_ = Round(
        goal="确认行为",
        actions=[RoundAction(title="运行测试", status=RoundItemStatus.COMPLETED)],
        unresolved=[],
        next_step="完成",
    )

    assert round_.has_evidence is False
    assert round_.is_complete is False


def test_open_questions_cannot_be_complete():
    round_ = Round(
        goal="确认边界内推进",
        evidence_refs=["tests/test_command_room_round.py::test_open_questions_cannot_be_complete passed"],
        open_questions=["是否需要用户确认下一轮"],
    )

    assert round_.has_evidence is True
    assert round_.is_complete is False


def test_user_confirmation_marks_round_incomplete():
    round_ = Round(
        goal="准备执行高风险下一步",
        evidence_refs=["tests/test_command_room_round.py::test_user_confirmation_marks_round_incomplete passed"],
        needs_user_confirmation=True,
        next_step="等待用户确认",
    )

    assert round_.needs_user_confirmation is True
    assert round_.is_complete is False


def test_next_round_out_of_boundary_cannot_be_complete():
    round_ = Round(
        goal="低风险落地补丁",
        evidence_refs=["pytest tests/test_command_room_round.py passed"],
        next_round=NextRound(
            proposal="修改生产配置",
            within_current_boundary=False,
            reason="超出当前低风险边界",
        ),
    )

    assert round_.next_round_is_safe is False
    assert round_.is_complete is False


def test_next_round_needing_confirmation_cannot_be_complete():
    round_ = Round(
        goal="低风险落地补丁",
        evidence_refs=["pytest tests/test_command_room_round.py passed"],
        next_round=NextRound(
            proposal="执行需要确认的迁移",
            needs_user_confirmation=True,
            reason="存在用户确认门槛",
        ),
    )

    assert round_.next_round_is_safe is False
    assert round_.is_complete is False


def test_evidence_without_open_questions_and_safe_next_round_can_complete():
    round_ = Round(
        goal="完成轮次契约修正",
        evidence_refs=["pytest tests/test_command_room_round.py passed"],
        state_delta=["补充轮次输入输出和下一轮建议"],
        verdict="满足最小契约",
        next_round=NextRound(
            proposal="继续在当前边界内清理文档",
            within_current_boundary=True,
            needs_user_confirmation=False,
        ),
    )

    assert round_.has_evidence is True
    assert round_.has_open_questions is False
    assert round_.next_round_is_safe is True
    assert round_.is_complete is True


def test_summary_is_short_and_natural():
    round_ = Round(
        goal="落地轮次 MVP",
        known_facts=["轮次是目标和边界内的状态推进"],
        actions=[
            RoundAction(title="检查现状", status=RoundItemStatus.COMPLETED),
            RoundAction(title="补测试", status=RoundItemStatus.PENDING),
        ],
        evidence_refs=["tests/test_command_room_round.py"],
        state_delta=["补充已知事实和未解问题字段"],
        verdict="可继续验证",
        next_round=NextRound(proposal="运行最小测试"),
    )

    summary = summarize_round(round_)

    assert "本轮围绕“落地轮次 MVP”推进" in summary
    assert "推进了补充已知事实和未解问题字段" in summary
    assert "依据是轮次是目标和边界内的状态推进；tests/test_command_room_round.py" in summary
    assert "下一步可继续：运行最小测试" in summary
    assert "|" not in summary
    assert len(summary) < 220


def test_completed_action_result_without_evidence_cannot_complete():
    round_ = Round(goal="接收动作结果").record_action_result(ActionResult(action_id="a1", description="检查文件", status=RoundItemStatus.COMPLETED))

    assert round_.action_results[0].is_done is True
    assert round_.has_evidence is False
    assert round_.is_complete is False


def test_action_result_with_evidence_and_no_questions_can_complete():
    round_ = Round(goal="验证动作结果").record_action_result(
        ActionResult(
            action_id="a1",
            description="运行测试",
            status=RoundItemStatus.COMPLETED,
            evidence_refs=["pytest tests/test_command_room_round.py passed"],
        )
    )

    assert round_.evidence_refs == ["pytest tests/test_command_room_round.py passed"]
    assert round_.is_complete is True


def test_action_result_open_questions_block_completion():
    round_ = Round(goal="验证动作问题").record_action_result(
        ActionResult(
            action_id="a1",
            status=RoundItemStatus.COMPLETED,
            evidence_refs=["test evidence"],
            open_questions=["还需确认输出口径"],
        )
    )

    assert round_.has_open_questions is True
    assert round_.is_complete is False


def test_action_result_conflicts_and_risks_block_completion():
    round_ = Round(goal="验证动作风险").record_action_result(
        ActionResult(
            action_id="a1",
            status=RoundItemStatus.COMPLETED,
            evidence_refs=["test evidence"],
            risks=["可能影响未提交改动"],
            conflicts=["结果与现有假设冲突"],
        )
    )

    assert round_.has_risks_or_conflicts is True
    assert round_.is_complete is False


def test_action_result_output_ref_is_not_evidence_but_is_summarized():
    round_ = Round(goal="记录动作输出").record_action_result(
        ActionResult(
            action_id="a1",
            description="生成报告",
            status=RoundItemStatus.COMPLETED,
            output_ref="outputs/report.md",
        )
    )

    summary = summarize_round(round_)

    assert round_.has_evidence is False
    assert round_.is_complete is False
    assert round_.action_results[0].output_ref == "outputs/report.md"
    assert "outputs/report.md" in summary


def test_action_result_summary_is_short_and_natural():
    round_ = Round(goal="整合动作结果").record_action_result(
        ActionResult(
            action_id="run-tests",
            description="运行最小测试",
            status=RoundItemStatus.COMPLETED,
            evidence_refs=["pytest passed"],
            output_ref="logs/pytest.txt",
        )
    )

    summary = summarize_round(round_)

    assert "动作“运行最小测试”已完成" in summary
    assert "输出见logs/pytest.txt" in summary
    assert "|" not in summary
    assert len(summary) < 220


def test_round_summarizes_evidence_strength_signals_without_verdict():
    round_ = Round(goal="汇总证据信号", evidence_refs=["tests passed"])

    signals = round_.evidence_signals()
    summary = summarize_round(round_)

    assert signals["strong_count"] == 0
    assert signals["weak_count"] == 1
    assert signals["quality_verdict"] is None
    assert signals["auto_rework"] is False
    assert "弱引用" in summary
    assert round_.is_complete is True


def test_tests_passed_alone_does_not_complete_without_evidence_ref():
    round_ = Round(goal="测试通过自述不等于完成").record_action_result(ActionResult(action_id="run-tests", summary="tests passed"))

    assert round_.has_evidence is False
    assert round_.is_complete is False


def test_command_output_evidence_is_strong_but_not_quality_verdict():
    round_ = Round(goal="记录强证据信号", evidence_refs=["command: python -m pytest tests/test_command_room_round.py; exit code: 0; stdout: passed"])

    signals = round_.evidence_signals()
    summary = summarize_round(round_)

    assert signals["has_strong_signal"] is True
    assert signals["strong_count"] == 1
    assert signals["quality_verdict"] is None
    assert signals["auto_rework"] is False
    assert "强引用" in summary
    assert round_.is_complete is True


def test_output_ref_does_not_contribute_to_evidence_signals():
    round_ = Round(goal="输出指针不是证据").record_action_result(ActionResult(action_id="write", output_ref="outputs/report.md"))

    signals = round_.evidence_signals()

    assert round_.evidence_refs == []
    assert signals["strong_count"] == 0
    assert signals["weak_count"] == 0
    assert round_.has_evidence is False
    assert round_.is_complete is False


def test_summary_user_confirmation_states_round_not_complete():
    round_ = Round(
        goal="确认锁处理下一步",
        evidence_refs=["只读检查 PID 后再决定是否清锁"],
        needs_user_confirmation=True,
        next_step="等待用户确认是否人工处理",
    )

    summary = summarize_round(round_)

    assert round_.is_complete is False
    assert "本轮尚未完成，下一步触及红线/授权边界，需要用户确认" in summary


def test_read_only_diagnostics_next_round_can_continue_autonomously():
    round_ = Round(
        goal="诊断锁文件阻塞",
        evidence_refs=["log: lock contention observed in worker output"],
        unresolved=["还需确认占锁 PID 是什么"],
        next_round=NextRound(
            proposal="只读查询 PID、查看相关日志和锁文件状态，再决定是否等待或请求授权清锁",
            within_current_boundary=True,
            needs_user_confirmation=False,
            reason="read-only diagnostics stay inside current boundary",
        ),
    )

    summary = summarize_round(round_)

    assert round_.is_complete is False
    assert round_.needs_user_confirmation is False
    assert round_.next_round_is_safe is True
    assert "本轮尚未完成但可继续自主排查" in summary
    assert "需要用户确认" not in summary


def test_destructive_boundary_next_round_requires_user_confirmation():
    round_ = Round(
        goal="处理锁文件阻塞",
        evidence_refs=["command: ps -p 1234 -o pid,comm; exit code: 0"],
        next_round=NextRound(
            proposal="kill PID 1234 并清理锁文件",
            within_current_boundary=True,
            needs_user_confirmation=True,
            reason="destructive process/file operation requires authorization",
        ),
    )

    summary = summarize_round(round_)

    assert round_.is_complete is False
    assert round_.needs_user_confirmation is False
    assert round_.next_round_is_safe is False
    assert "触及红线/授权边界，需要用户确认" in summary
