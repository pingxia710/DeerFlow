from deerflow.agents.middlewares.round_context_middleware import (
    format_round_context_for_model,
    latest_round_context_for_thread,
)
from deerflow.command_room.round_record import record_command_room_round
from deerflow.subagents.audit import record_subagent_handoff


def _record(agent_name="command-room", signals=True, required=True):
    return {
        "agentName": agent_name,
        "roundRequired": required,
        "roundBrief": {
            "goal": "inspect backend tests",
            "boundaries": ["read-only", "no credentials"],
            "handoff_signals": ["inspect: command exited 0"],
            "summary": "Evidence: 1 weak evidence signal; Next safe action: inspect backend tests",
            "evidence_status": "1 weak evidence signal(s); treat worker self-claims as untrusted",
            "next_safe_action": "inspect backend tests",
        },
        "roundContextSignals": {
            "action_count": 1,
            "risks": ["risk-a", "risk-b", "risk-c", "risk-d"],
            "conflicts": ["conflict-a"],
            "open_questions": ["what remains?"],
            "unresolved": ["missing output path"],
            "evidence_signals": {"evidence_state": "STALE"},
            "summary": "long worker output must not appear",
            "needs_user_confirmation": True,
            "requires_confirmation": True,
            "round_complete": False,
            "next_round_is_safe": False,
            "quality_verdict": "PASS",
            "auto_rework": True,
        },
    }


def test_command_room_context_injects_only_objective_working_memory():
    text = format_round_context_for_model(_record())

    assert text is not None
    assert "Internal Command Room Round signals" in text
    assert "Current user goal: inspect backend tests" in text
    assert "Boundary: read-only; no credentials" in text
    assert "Action occurred: inspect: command exited 0" in text
    assert "Risk: risk-a; risk-b; risk-c" in text
    assert "Conflict: conflict-a" in text
    assert "Unresolved question: what remains?" in text
    assert "Unresolved: missing output path" in text
    assert "risk-d" not in text
    lowered = text.lower()
    for prohibited in (
        "evidence_status",
        "evidence=",
        "round_complete",
        "next_round_is_safe",
        "quality_verdict",
        "auto_rework",
        "next_safe_action",
        "next safe action",
        "worker self-claims",
        "pass",
    ):
        assert prohibited not in lowered


def test_round_context_without_actual_facts_does_not_inject_governance_shell():
    record = {"roundRequired": True, "roundContextSignals": {"action_count": 0}}
    assert format_round_context_for_model(record) is None
    assert format_round_context_for_model({"roundRequired": True}) is None
    assert format_round_context_for_model(_record(required=False)) is None
    assert format_round_context_for_model(None) is None


def test_round_context_recovers_objective_facts_from_subagent_handoff_file(tmp_path, monkeypatch):
    thread_id = "thread-1"
    user_id = "user-1"
    run_id = "run-1"
    fake_thread_dir = tmp_path / "thread"

    class Paths:
        def thread_dir(self, thread_id, user_id=None):
            return fake_thread_dir

    monkeypatch.setattr("deerflow.subagents.audit.get_paths", lambda: Paths())
    monkeypatch.setattr("deerflow.command_room.round_record.get_paths", lambda: Paths())

    record_subagent_handoff(
        thread_id=thread_id,
        run_id=run_id,
        task_id="task-1",
        trace_id="trace-1",
        user_id=user_id,
        subagent_type="fact-finder",
        description="inspect recovery path",
        prompt="""Goal: inspect recovery path
Boundary: read-only
Expected Evidence: command refs
Stop Conditions: stop before credentials
Capabilities: read files
RAW_PROMPT_SECRET
""",
        status="completed",
        result="""Role: fact-finder
Claim: Recovery path produced observable evidence.
EvidenceRefs: command: pytest tests/test_round_context_injection.py -q; exit code: 0
EvidenceState: SUPPORTED
SelfAttestationOnly: false
Unknown/Stale: none
Conflicts: none
RedlineTouched: false
RecommendedDecision: NEEDS_MORE
NextAction: continue bounded review
RAW_RESULT_SECRET
""",
        action_result={
            "action_id": "task-1",
            "status": "completed",
            "summary": "handoff recovery action summary",
            "evidence_refs": ["command: pytest tests/test_round_context_injection.py -q; exit code: 0"],
            "next_step": "continue bounded review",
        },
    )

    path = record_command_room_round(
        thread_id=thread_id,
        agent_name="command-room",
        user_id=user_id,
        run_id=run_id,
        user_message="recover next round context",
        final_text="""Round Card
Goal: recover next round context
Boundary: read-only; no production or credentials
Evidence: command refs collected
Verdict: NEEDS_MORE
Next: continue bounded review
""",
    )

    assert path == fake_thread_dir / "audit" / "command_room_rounds.jsonl"
    text = latest_round_context_for_thread(thread_id, user_id)
    assert text is not None
    assert "Persisted user goal fact: text fingerprint recorded" in text
    assert "Explicit boundary: read-only" in text
    assert "Action occurred:" in text
    assert "task task-1" in text
    assert "status completed" in text
    assert "description: inspect recovery path" in text
    lowered = text.lower()
    for prohibited in (
        "evidence_state",
        "pass",
        "round_complete",
        "next_safe_action",
        "accepted_next_action",
        "raw_prompt_secret",
        "raw_result_secret",
    ):
        assert prohibited not in lowered
