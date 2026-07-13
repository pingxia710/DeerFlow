import json
from types import SimpleNamespace

from deerflow.agents.lead_agent import prompt as prompt_module
from deerflow.agents.middlewares import round_context_middleware as round_context_module
from deerflow.subagents.audit import record_subagent_handoff
from deerflow.subagents.builtins.command_room_roles import COMMAND_ROOM_ROLE_CONFIGS


def test_command_room_prompt_uses_ai_ai_ai_without_generic_clarification_gate(monkeypatch):
    monkeypatch.setattr(prompt_module, "get_agent_soul", lambda agent_name=None: "")
    monkeypatch.setattr(prompt_module, "get_skills_prompt_section", lambda *args, **kwargs: "")
    monkeypatch.setattr(prompt_module, "get_deferred_tools_prompt_section", lambda **kwargs: "")
    monkeypatch.setattr(prompt_module, "_build_acp_section", lambda **kwargs: "")
    monkeypatch.setattr(prompt_module, "_build_local_host_access_section", lambda **kwargs: "")
    monkeypatch.setattr(prompt_module, "_build_custom_mounts_section", lambda **kwargs: "")

    prompt = prompt_module.apply_prompt_template(agent_name="command-room")

    assert "COMMAND ROOM AI-AI-AI" in prompt
    assert "Delegate execution to one-shot sub-AIs" in prompt
    assert "another sub-AI check each worker result" in prompt
    assert "independent opposition view" in prompt
    assert "Do not defer an in-scope safe next action to a later turn" in prompt
    assert "MANDATORY Clarification Scenarios" not in prompt
    assert "CLARIFY → PLAN → ACT" not in prompt
    assert "Clarification ALWAYS comes BEFORE action" not in prompt


def test_command_room_does_not_reinject_persisted_round_audit(monkeypatch):
    monkeypatch.setattr(round_context_module, "latest_round_context_for_thread", lambda *_: "[Internal Command Room Round signals]\nstale audit action")
    middleware = round_context_module.CommandRoomRoundContextMiddleware(agent_name="command-room")

    text = middleware._context_text(
        SimpleNamespace(
            context={
                "thread_id": "thread-1",
                "round_context": {"current_intent": "fix the conversation scroll"},
            }
        )
    )

    assert text is not None
    assert "Current user goal: fix the conversation scroll" in text
    assert "stale audit action" not in text


def test_subagent_audit_does_not_infer_a_verdict_from_worker_prose(tmp_path):
    path = record_subagent_handoff(
        thread_id="thread-1",
        run_id="run-1",
        task_id="task-1",
        trace_id="trace-1",
        user_id="user-1",
        subagent_type="general-purpose",
        description="inspect the change",
        prompt="inspect the change",
        status="completed",
        result="EvidenceRefs: worker self-claims only\nRecommendedDecision: NEEDS_MORE\nTarget Role: opposition",
        base_dir=tmp_path,
    )

    assert path is not None
    record = json.loads(path.read_text(encoding="utf-8"))
    assert "signal" not in record
    assert "output_handoff_packet" not in record
    assert record["result_sha256"]


def test_command_room_role_catalog_does_not_install_program_workflows():
    role = COMMAND_ROOM_ROLE_CONFIGS["opposition"]

    assert "Opposition role" in role.description
    assert role.system_prompt == role.description
    assert not hasattr(role, "skills")
