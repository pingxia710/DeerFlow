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
    assert "files, code, logs, plans, and artifacts directly with configured tools" in prompt
    assert "EVERY RUN — the five-step contract" in prompt
    assert "turn every dependency-satisfied card into a task" in prompt
    assert "RECONCILE EVERY RUN" in prompt
    assert "INTENT RECEIPT" in prompt
    assert "do not delegate merely to preserve context" in prompt
    assert "FIRST — classify every request before any tool call" in prompt
    assert "Read-only discovery—locating a project" in prompt
    assert "Direct-handling example" in prompt
    assert "do not create a Goal Mandate, Brief, Organization Map, an Opposition task" in prompt
    assert "Read applicable `AGENTS.md`, `Progress.md`" in prompt
    assert "Progress is factual memory, never authority" in prompt
    assert "Make that AI-AI contract self-contained" in prompt
    assert "exact working/input/output paths" in prompt
    assert "Read every complete natural result and choose every next action yourself" in prompt
    assert "Ordinary safe, bounded work explicitly requested by the human is authorized" in prompt
    assert "Never require or present a plan for read-only discovery" in prompt
    assert "Use Chair plan → human discussion only" in prompt
    assert "five human gates" in prompt
    assert "1. A new or changed Goal Mandate, value priority, or non-goal." in prompt
    assert "2. A material architecture, operating model, workflow, or route change." in prompt
    assert "3. A material trade-off the current intent cannot resolve." in prompt
    assert "4. A new real external, irreversible, or sensitive permission." in prompt
    assert "5. The Owner explicitly requests review." in prompt
    assert "Run one Opposition challenge for every new root goal" in prompt
    assert "Do not create a new Brief solely for a task receipt" in prompt
    assert "a result acknowledgement, or a history read" in prompt
    assert "A single simple workstream does not need an Organization Map" in prompt
    assert "never a program state or gate" in prompt
    assert "Six is resource capacity, not a task-count target" in prompt
    assert "Goal Mandate" in prompt
    assert "record_goal_workspace" in prompt
    assert "Current Organization Map" in prompt
    assert "project-manager" in prompt
    assert "Continue the current plan directly after a phase result" in prompt
    assert "Prefer a matching fixed professional role" in prompt
    assert "only for a genuinely one-off perspective" in prompt
    assert "six outstanding child jobs" in prompt
    assert "a Recorder child cannot substitute" in prompt
    assert "read_goal_workspace_history" in prompt
    assert "one bounded raw page" in prompt
    assert "read_workspace_results" in prompt
    assert "re-dispatch exactly once with a new task id" in prompt
    assert "Do not automatically retry a cancelled or interrupted task" in prompt
    assert "acknowledge_workspace_results" in prompt
    assert "create_goal_cell" in prompt
    assert "return_to_parent" in prompt
    assert "capability references never expand" in prompt
    assert "never copies an entire parent Workspace" in prompt
    assert "relevant factual revisions with complete bodies" in prompt
    assert "copies their bytes without choosing relevance or judging them" in prompt
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
    assert "Failure never authorizes redispatch" not in prompt
    assert "four human gates" not in prompt


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
        "project-manager",
        "executor",
        "fact-finder",
        "opposition",
        "recorder",
        "runtime-reliability-auditor",
        "persistence-migration-auditor",
        "frontend-protocol-auditor",
        "security-auditor",
        "platform-ops-auditor",
    }
    for role in COMMAND_ROOM_ROLE_CONFIGS.values():
        assert role.system_prompt == role.description
        assert not hasattr(role, "skills")
    assert "after a Chair draft plan" in COMMAND_ROOM_ROLE_CONFIGS["opposition"].description
    assert "no material challenge exists" in COMMAND_ROOM_ROLE_CONFIGS["opposition"].description
    assert "does not approve" in COMMAND_ROOM_ROLE_CONFIGS["opposition"].description
    assert "restart" in COMMAND_ROOM_ROLE_CONFIGS["runtime-reliability-auditor"].description
    assert "each supported database" in COMMAND_ROOM_ROLE_CONFIGS["persistence-migration-auditor"].description
    assert "end-to-end proof" in COMMAND_ROOM_ROLE_CONFIGS["frontend-protocol-auditor"].description
    assert "reproduced exploits" in COMMAND_ROOM_ROLE_CONFIGS["security-auditor"].description
    assert "supply-chain" in COMMAND_ROOM_ROLE_CONFIGS["platform-ops-auditor"].description
