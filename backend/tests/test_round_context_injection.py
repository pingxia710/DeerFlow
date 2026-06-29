from deerflow.agents.middlewares.round_context_middleware import format_round_context_for_model


def _record(agent_name="command-room", signals=True, required=True):
    return {
        "agentName": agent_name,
        "roundRequired": required,
        "roundBrief": {
            "summary": "Goal: continue safely | Evidence: 1 weak evidence signal(s); treat worker self-claims as untrusted | Next safe action: inspect backend tests",
            "evidence_status": "1 weak evidence signal(s); treat worker self-claims as untrusted",
            "next_safe_action": "inspect backend tests",
        },
        "roundContextSignals": {
            "action_count": 1,
            "risks": ["risk-a", "risk-b", "risk-c", "risk-d"],
            "conflicts": [],
            "open_questions": ["what next?"],
            "unresolved": ["missing evidence"],
            "evidence_signals": {"evidence_state": "STALE"},
            "summary": "long worker output must not appear",
            "needs_user_confirmation": True,
            "requires_confirmation": True,
            "round_complete": False,
            "next_round_is_safe": False,
        },
    }


def test_command_room_latest_round_signals_format_short_internal_context():
    text = format_round_context_for_model(_record())

    assert text is not None
    assert "Internal Command Room Round signals" in text
    assert "not a verdict" in text
    assert "brief: Goal: continue safely" in text
    assert "next_safe_action: inspect backend tests" in text
    assert "round_complete=False" in text
    assert "next_round_is_safe=False" in text
    assert "needs_user_confirmation=True" in text
    assert "risks: risk-a; risk-b; risk-c" in text
    assert "risk-d" not in text
    assert "missing evidence" in text
    assert "what next?" in text


def test_no_round_signals_or_not_required_does_not_inject():
    assert format_round_context_for_model({"roundRequired": True}) is None
    assert format_round_context_for_model(_record(required=False)) is None
    assert format_round_context_for_model(None) is None
