"""Tests for natural-language AI role metadata."""

from dataclasses import fields
from pathlib import Path

from deerflow.config.subagents_config import CustomSubagentConfig, SubagentsAppConfig
from deerflow.subagents.builtins.command_room_roles import COMMAND_ROOM_ROLE_CONFIGS, COMMAND_ROOM_ROLE_SKILLS
from deerflow.subagents.config import SubagentConfig
from deerflow.subagents.registry import get_subagent_config, get_subagent_names


def _read_custom_skill(repo_root: Path, skill_name: str) -> str:
    skill_file = repo_root / "skills" / "custom" / skill_name / "SKILL.md"
    return skill_file.read_text(encoding="utf-8")


def test_command_room_opposition_skill_challenges_the_completed_plan():
    repo_root = Path(__file__).resolve().parents[2]
    opposition = _read_custom_skill(repo_root, "command-room-opposition")

    assert "original self-contained Chair brief and the complete Chair draft plan" in opposition
    assert "hidden assumptions" in opposition
    assert "no material challenge exists" in opposition
    assert "Do not approve, reject" in opposition


def test_nextos_commander_defines_the_ai_enterprise_without_program_control():
    repo_root = Path(__file__).resolve().parents[2]
    skill = _read_custom_skill(repo_root, "nextos-commander")

    assert "AI organization layer built on the DeerFlow runtime" in skill
    assert "Treat ordinary safe, bounded work explicitly requested by the human as already" in skill
    assert "Read-only discovery—locating a project" in skill
    assert "do not create a Mandate, Brief, Organization Map, an Opposition task" in skill
    assert "Use the escalated sequence only for a changed Goal Mandate" in skill
    assert "Draft the complete plan yourself" in skill
    assert "mandatory for new root goals, new or materially revised plans, and material route changes" in skill
    assert "Opposition challenge" in skill
    assert "directly execute only the four direct-work" in skill
    assert "five-step contract" in skill
    assert "read_workspace_results" in skill
    assert "self-claim is not evidence" in skill
    assert "Present it and pause for human discussion" in skill
    assert "Explicit natural-language confirmation" in skill
    assert "Six slots are resource capacity, not a quota" in skill
    assert "Every child handoff names the exact working, input, and output paths" in skill
    assert "Goal Mandate" in skill
    assert "The Chair itself calls `record_goal_workspace`" in skill
    assert "Current Organization Map" in skill
    assert "`read_goal_workspace_history`" in skill
    assert "do not automatically" in skill
    assert "load the whole history" in skill
    assert "project-manager" in skill
    assert "Continue after a phase-level report unless it introduces an escalation" in skill
    assert "twelve child processes" in skill
    assert "not task-level acceptance" in skill
    assert "Workstream Lead" in skill
    assert "temporary independent checking perspective" in skill
    assert "## Governance learning" in skill
    assert "applicable `Progress.md`" in skill
    assert "Programs never count" in skill
    assert "failures or update governance" in skill
    assert "Programs only transport" in skill
    assert "`input_refs`" in skill
    assert "neither selects relevance nor evaluates their content" in skill
    assert "programmatic role router" not in skill


def test_specialist_auditor_skills_keep_narrow_evidence_methods():
    repo_root = Path(__file__).resolve().parents[2]
    expectations = {
        "command-room-runtime-reliability-auditor": (
            "Trace the assigned lifecycle end to end",
            "cancellation",
            "Must not: implement fixes",
        ),
        "command-room-persistence-migration-auditor": (
            "Evaluate every supported database separately",
            "PostgreSQL behavior",
            "mutate production or live business data",
        ),
        "command-room-frontend-protocol-auditor": (
            "real end-to-end observation",
            "claim end-to-end proof from static or unit tests",
            "Must not: implement fixes",
        ),
        "command-room-security-auditor": (
            "label each finding as reproduced, code-supported, or hypothetical",
            "print credentials",
            "Must not:",
        ),
        "command-room-platform-ops-auditor": (
            "infer full readiness from `/health` or a Compose parse",
            "mutate production or external services",
            "usable service path",
        ),
    }

    for skill_name, phrases in expectations.items():
        text = _read_custom_skill(repo_root, skill_name)
        assert "Version: 0.1.0" in text, skill_name
        for phrase in phrases:
            assert phrase in text, (skill_name, phrase)


def test_command_room_fact_finder_skill_is_bounded_and_read_only():
    repo_root = Path(__file__).resolve().parents[2]
    text = _read_custom_skill(repo_root, "command-room-fact-finder")

    assert "read-only" in text
    assert "direct observations" in text
    assert "Do not decide, authorize" in text


def test_project_manager_proposes_without_advancing_work():
    repo_root = Path(__file__).resolve().parents[2]
    text = _read_custom_skill(repo_root, "command-room-project-manager")

    assert "proposed next objective" in text
    assert "relevant factual" in text
    assert "facts and\nassumptions" in text
    assert "Must not: start work" in text
    assert "advance" in text


def test_command_room_recorder_preserves_only_durable_facts_when_useful():
    repo_root = Path(__file__).resolve().parents[2]
    text = _read_custom_skill(repo_root, "command-room-recorder")

    assert "natural-language record already made by the lead AI" in text
    assert "Do not choose, alter, expand, validate, or improve" in text
    assert "Do not infer" in text
    assert "`Progress.md`" in text


def test_role_catalog_contains_metadata_not_execution_controls():
    assert {field.name for field in fields(SubagentConfig)} == {"name", "description", "system_prompt"}
    assert COMMAND_ROOM_ROLE_CONFIGS["opposition"].system_prompt
    assert "strongest materially different alternative" in COMMAND_ROOM_ROLE_CONFIGS["opposition"].description


def test_each_configured_command_room_role_has_a_role_charter_and_skill():
    repo_root = Path(__file__).resolve().parents[2]

    chair_dir = repo_root / "skills" / "custom" / "nextos-commander"
    assert (chair_dir / "AGENTS.md").is_file()
    assert (chair_dir / "SKILL.md").is_file()
    assert set(COMMAND_ROOM_ROLE_SKILLS) == set(COMMAND_ROOM_ROLE_CONFIGS)
    for role_name, skill_name in COMMAND_ROOM_ROLE_SKILLS.items():
        role_dir = repo_root / "skills" / "custom" / skill_name
        assert (role_dir / "AGENTS.md").is_file(), role_name
        assert (role_dir / "SKILL.md").is_file(), role_name


def test_command_room_role_skills_keep_minimal_frontmatter_and_body_version():
    repo_root = Path(__file__).resolve().parents[2]
    skill_names = {"nextos-commander", *COMMAND_ROOM_ROLE_SKILLS.values()}

    for skill_name in skill_names:
        text = _read_custom_skill(repo_root, skill_name)
        frontmatter = text.split("---", 2)[1]
        keys = {line.split(":", 1)[0] for line in frontmatter.splitlines() if ":" in line}
        assert keys == {"name", "description"}, skill_name
        expected_versions = {"nextos-commander": "0.7.1", "command-room-opposition": "0.2.0", "command-room-executor": "0.1.1"}
        expected_version = expected_versions.get(skill_name, "0.1.0")
        assert f"Version: {expected_version}" in text, skill_name


def test_custom_role_keeps_natural_language_role_prompt_but_not_program_controls():
    role = CustomSubagentConfig.model_validate(
        {
            "description": "Inspects frontend and backend contracts.",
            "system_prompt": "Act as a professional contract inspector.",
            "model": "gpt-5.6-terra",
            "tools": ["bash"],
            "max_turns": 80,
        }
    )

    assert role.model_dump() == {
        "description": "Inspects frontend and backend contracts.",
        "system_prompt": "Act as a professional contract inspector.",
        "model": "gpt-5.6-terra",
    }


def test_registry_exposes_custom_role_to_the_lead():
    app_config = SubagentsAppConfig(custom_agents={"contract-inspector": {"description": "Inspects frontend and backend contracts."}})

    role = get_subagent_config("contract-inspector", app_config=app_config)

    assert role == SubagentConfig(name="contract-inspector", description="Inspects frontend and backend contracts.")
    assert "contract-inspector" in get_subagent_names(app_config=app_config)


def test_registry_allows_local_command_room_executor_override():
    description = "Local executor role context."
    app_config = SubagentsAppConfig(custom_agents={"executor": {"description": description}})

    role = get_subagent_config("executor", app_config=app_config)

    assert role == SubagentConfig(name="executor", description=description)
