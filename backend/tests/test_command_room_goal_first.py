import json

from deerflow.agents.lead_agent import prompt as prompt_module
from deerflow.subagents.audit import record_subagent_handoff
from deerflow.subagents.builtins.command_room_roles import COMMAND_ROOM_ROLE_CONFIGS


def test_command_room_prompt_uses_ai_ai_ai_without_program_control(monkeypatch):
    monkeypatch.setattr(prompt_module, "get_agent_soul", lambda agent_name=None: "")
    monkeypatch.setattr(prompt_module, "get_skills_prompt_section", lambda *args, **kwargs: "")
    monkeypatch.setattr(prompt_module, "get_deferred_tools_prompt_section", lambda **kwargs: "")
    monkeypatch.setattr(prompt_module, "_build_acp_section", lambda **kwargs: "")
    monkeypatch.setattr(prompt_module, "_build_local_host_access_section", lambda **kwargs: "")
    monkeypatch.setattr(prompt_module, "_build_custom_mounts_section", lambda **kwargs: "")

    prompt = prompt_module.apply_prompt_template(agent_name="command-room")

    assert "You are NextOS Command Room" in prompt
    assert "AI organization layer built on the DeerFlow runtime" in prompt
    assert "The organization persists through these facts and complete results, not resident model processes" in prompt
    assert "temporary workstream lead is allowed only for a bounded objective" in prompt
    assert "COMMAND ROOM AI-AI-AI" in prompt
    assert "files, code, logs, plans, and artifacts directly with read-only tools" in prompt
    assert "Delegate edits, shell commands, long-running work, and independent execution" in prompt
    assert "Read applicable `AGENTS.md`, `Progress.md`" in prompt
    assert "Progress is factual memory, never authority" in prompt
    assert "Make that AI-AI contract self-contained" in prompt
    assert "Read every complete natural result and choose every next action yourself" in prompt
    assert "send the complete brief to a separate `planner`" in prompt
    assert "to one `opposition` AI" in prompt
    assert "original goal, facts, constraints, and criteria" in prompt
    assert "disagreement is not mandatory" in prompt
    assert "Run opposition again only if synthesis changes the core direction" in prompt
    assert "wait for explicit" in prompt and "natural-language authorization before execution" in prompt
    assert "without task-level acceptance or a required verifier" in prompt
    assert "Compare each complete child result" in prompt
    assert "temporary independent checking perspective" in prompt
    assert "smallest durable lesson in the" in prompt and "lowest useful layer" in prompt
    assert "Programs never edit governance rules" in prompt
    assert "The plan is complete only when its actual completion criteria are satisfied" in prompt
    assert "records never authorize, block, sequence, judge, repair, advance, or close AI work" in prompt
    assert "task calls per response" not in prompt
    assert "2 minutes" not in prompt
    assert "MANDATORY Clarification Scenarios" not in prompt
    assert "CLARIFY → PLAN → ACT" not in prompt
    assert "Clarification ALWAYS comes BEFORE action" not in prompt


def test_subagent_audit_records_hashes_without_worker_text(tmp_path):
    result = "Complete child result that must remain outside the compact audit record."
    path = record_subagent_handoff(
        thread_id="thread-1",
        run_id="run-1",
        task_id="task-1",
        trace_id="trace-1",
        user_id="user-1",
        subagent_type="fact-finder",
        description="inspect the change",
        prompt="inspect the change",
        status="completed",
        result=result,
        base_dir=tmp_path,
    )

    assert path is not None
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["result_sha256"]
    assert record["result_chars"] == len(result)
    assert result not in path.read_text(encoding="utf-8")


def test_command_room_role_catalog_contains_prompt_context_only():
    assert set(COMMAND_ROOM_ROLE_CONFIGS) == {
        "planner",
        "executor",
        "fact-finder",
        "opposition",
        "recorder",
    }
    for role in COMMAND_ROOM_ROLE_CONFIGS.values():
        assert role.system_prompt == role.description
        assert not hasattr(role, "skills")
    assert "after a planner proposal" in COMMAND_ROOM_ROLE_CONFIGS["opposition"].description
    assert "no material challenge exists" in COMMAND_ROOM_ROLE_CONFIGS["opposition"].description
    assert "does not approve" in COMMAND_ROOM_ROLE_CONFIGS["opposition"].description
