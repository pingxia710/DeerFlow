from deerflow.agents.middlewares.round_context_middleware import (
    format_capability_snapshot_for_model,
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


def test_capability_snapshot_format_includes_tools_and_stop_before_risks():
    text = format_capability_snapshot_for_model(
        {
            "tools": [{"name": "read_file"}, {"name": "bash"}],
            "approval_policy": {"stop_before": ["credential disclosure", "production writes"]},
            "sandbox": {
                "use": "deerflow.sandbox.local:LocalSandboxProvider",
                "host_bash_available": False,
                "unrestricted_host_access": False,
            },
        }
    )

    assert text is not None
    assert "Internal Capability Snapshot" in text
    assert "enabled_tools: read_file, bash" in text
    assert "stop_before: credential disclosure; production writes" in text
    lowered = text.lower()
    assert "pass" not in lowered
    assert "fail" not in lowered


def test_round_context_recovers_from_subagent_handoff_file(tmp_path, monkeypatch):
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
    assert "brief: Goal: recover next round context" in text
    assert "trusted observable evidence" in text
    assert "动作“task-1”已完成" in text
    assert "actions=1" in text
    lowered = text.lower()
    assert "gate" not in lowered
    assert "pass" not in lowered
    assert "fail" not in lowered
    assert "raw_prompt_secret" not in lowered
    assert "raw_result_secret" not in lowered
