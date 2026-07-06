from deerflow.command_room.context_compaction import COMPACTION_MARKER, ContextBlock, compact_command_room_context_blocks


def test_compaction_keeps_high_priority_and_omits_low_priority_when_over_budget():
    text = compact_command_room_context_blocks(
        [
            ContextBlock(name="account", priority=80, content="ledger " + ("l" * 400)),
            ContextBlock(name="history", priority=90, content="old history " + ("h" * 400)),
            ContextBlock(name="native_round", priority=10, content="native round state: blocked; do not decide automatically"),
        ],
        max_chars=180,
        block_budgets={"native_round": 160, "account": 160, "history": 160},
    )

    assert text is not None
    assert "native round state" in text
    assert "old history" not in text
    assert "no automatic decision" in text.lower() or "no automatic" in text.lower()


def test_compaction_prioritizes_waiting_user_pending_handoff_boundary_over_ledger():
    text = compact_command_room_context_blocks(
        [
            {"name": "account", "priority": 80, "content": "ledger details " + ("x" * 300)},
            {"name": "waiting_user", "priority": 1, "content": "waiting_user: need explicit user confirmation before continuing"},
            {"name": "pending_handoffs", "priority": 20, "content": "pending handoff: ask evidence role"},
            {"name": "boundary", "priority": 0, "content": "boundary: do not read secrets"},
        ],
        max_chars=220,
        block_budgets={"waiting_user": 100, "pending_handoffs": 80, "boundary": 80, "account": 80},
    )

    assert text is not None
    assert "waiting_user" in text
    assert "pending handoff" in text
    assert "boundary: do not read secrets" in text
    assert "ledger details" not in text


def test_compaction_marker_is_advisory_and_non_decisive_on_truncation():
    text = compact_command_room_context_blocks(
        [ContextBlock(name="quality", priority=60, content="AI-authored recommendations only. Chair decides next steps; no automatic dispatch or rework. " + ("q" * 500))],
        max_chars=420,
        block_budgets={"quality": 180},
    )

    assert text is not None
    assert "Chair decides next steps" in text
    assert "truncated" in text.lower()
    assert COMPACTION_MARKER.split(":", 1)[0] in text
    lowered = text.lower()
    assert "no automatic" in lowered
    assert "verdict" in lowered
