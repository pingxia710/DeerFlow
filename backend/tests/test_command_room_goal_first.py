import json
from types import SimpleNamespace

from deerflow.agents.lead_agent import prompt as prompt_module
from deerflow.agents.middlewares import round_context_middleware as round_context_module
from deerflow.config.paths import Paths
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
    assert "goal, boundary, and observable result are clear, skip Planning" in prompt
    assert "planning-forward" in prompt
    assert "planning-opposition" in prompt
    assert 'container="execution", delivery_cycle_index=N' in prompt
    assert 'container="review", delivery_cycle_index=N' in prompt
    assert "2 minutes" not in prompt
    assert "Do not defer an in-scope safe next action to a later turn" in prompt
    assert "MANDATORY Clarification Scenarios" not in prompt
    assert "CLARIFY → PLAN → ACT" not in prompt
    assert "Clarification ALWAYS comes BEFORE action" not in prompt


def test_command_room_context_includes_shared_ai_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(round_context_module, "get_paths", lambda: Paths(tmp_path))
    middleware = round_context_module.CommandRoomRoundContextMiddleware(agent_name="command-room")

    text = middleware._context_text(
        SimpleNamespace(
            context={
                "thread_id": "thread-1",
                "run_id": "run-1",
                "user_id": "user-1",
            }
        )
    )

    workspace = tmp_path / "users" / "user-1" / "threads" / "thread-1" / "user-data" / "workspace"
    ai_workspace = workspace / "command-room-loop" / "thread-1"
    assert text is not None
    assert "[Internal AI-AI Workspace]" in text
    assert str(ai_workspace / "01-planning" / "spec.md") in text
    assert (ai_workspace / "02-technical-design" / "technical-plan.md").is_file()
    assert (ai_workspace / "03-delivery" / "README.md").is_file()


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
    evaluator = COMMAND_ROOM_ROLE_CONFIGS["evaluator"]

    assert "Opposition angle" in role.description
    assert role.system_prompt == role.description
    assert not hasattr(role, "skills")
    assert "evaluator role" in evaluator.description
    assert evaluator.system_prompt == evaluator.description
