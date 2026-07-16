"""Tests for AI role metadata and Command Room governing skills."""

from dataclasses import fields
from pathlib import Path

import pytest

from deerflow.config.subagents_config import CustomSubagentConfig, SubagentsAppConfig
from deerflow.subagents.builtins.command_room_roles import COMMAND_ROOM_ROLE_CONFIGS
from deerflow.subagents.config import SubagentConfig
from deerflow.subagents.registry import get_subagent_config, get_subagent_names


def _read_custom_skill_or_skip(repo_root: Path, skill_name: str) -> str:
    skill_file = repo_root / "skills" / "custom" / skill_name / "SKILL.md"
    if not skill_file.exists():
        pytest.skip(f"custom skill is ignored and not present: {skill_file}")
    return skill_file.read_text(encoding="utf-8")


def test_command_room_evidence_and_opposition_skills_are_ai_roles():
    repo_root = Path(__file__).resolve().parents[2]
    evidence = _read_custom_skill_or_skip(repo_root, "command-room-evidence")
    opposition = _read_custom_skill_or_skip(repo_root, "command-room-opposition")

    assert "checks proportionate to the deliverable" in evidence
    assert "Worker prose is not proof" in evidence
    assert "Start independently from the same Chair brief" in opposition
    assert "hidden assumptions" in opposition


def test_command_room_capability_governor_is_limited_to_material_expansion():
    repo_root = Path(__file__).resolve().parents[2]
    text = _read_custom_skill_or_skip(repo_root, "command-room-capability-governor")

    assert "narrowest safe scope" in text
    assert "whether user authorization is needed" in text
    assert "Do not authorize it yourself" in text


def test_command_room_fact_finder_skill_is_bounded_and_read_only():
    repo_root = Path(__file__).resolve().parents[2]
    text = _read_custom_skill_or_skip(repo_root, "command-room-fact-finder")

    assert "read-only" in text
    assert "direct observations" in text
    assert "Do not decide, authorize" in text
    assert "review workflow" in text


def test_command_room_chair_skill_keeps_the_lead_as_final_judge():
    repo_root = Path(__file__).resolve().parents[2]
    text = _read_custom_skill_or_skip(repo_root, "command-room-chair")

    assert "user's goal" in text
    assert "Independent forward and opposition angles" in text
    assert "Freely dispatch a bounded background task" in text
    assert "final judgment" in text


def test_command_room_recorder_preserves_only_durable_facts_when_useful():
    repo_root = Path(__file__).resolve().parents[2]
    text = _read_custom_skill_or_skip(repo_root, "command-room-recorder")

    assert "exact natural-language record" in text
    assert "Do not choose, alter, expand, validate, or improve" in text
    assert "Do not infer permission to update `Progress.md`" in text


def test_nextos_commander_encodes_ai_ai_ai_without_program_control():
    repo_root = Path(__file__).resolve().parents[2]
    skill = _read_custom_skill_or_skip(repo_root, "nextos-commander")

    assert "Delegate execution through self-contained natural-language prompts" in skill
    assert "Package, container, artifact, and cycle fields are optional facts only" in skill
    assert "freely decides when a Review is useful" in skill
    assert "independent forward and opposition AIs" in skill
    assert "Do not build a programmatic role roster" in skill


def test_nextos_commander_skillopt_entrypoint_runs_behavior_gate():
    repo_root = Path(__file__).resolve().parents[2]
    script = (repo_root / "scripts" / "skillopt-probe.sh").read_text(encoding="utf-8")

    assert "command-room-skill-behavior-probe.py" in script
    assert "SKILLOPT_STATIC_ONLY" in script
    assert "behavior_report.json" in script

    behavior_probe = (repo_root / "scripts" / "command-room-skill-behavior-probe.py").read_text(encoding="utf-8")
    assert 'payload.get("passed") is True' in behavior_probe
    assert "evaluate_decisions" not in behavior_probe
    assert "next_action" not in behavior_probe


def test_command_room_docs_keep_routing_with_the_lead_ai():
    repo_root = Path(__file__).resolve().parents[2]
    protocol = (repo_root / "docs" / "command-room" / "ai-control-protocol.md").read_text(encoding="utf-8")

    assert "clear bounded action" in protocol
    assert "Planning angles" in protocol
    assert "Execution -> Review" in protocol
    assert "The Chair alone decides" in protocol
    assert "must not parse prose" in protocol


def test_role_catalog_contains_metadata_not_execution_controls():
    assert {field.name for field in fields(SubagentConfig)} == {"name", "description", "system_prompt"}
    assert COMMAND_ROOM_ROLE_CONFIGS["opposition"].system_prompt
    assert "Opposition angle" in COMMAND_ROOM_ROLE_CONFIGS["opposition"].description


def test_custom_role_keeps_natural_language_role_prompt_but_not_program_controls():
    role = CustomSubagentConfig.model_validate(
        {
            "description": "Checks frontend and backend contracts.",
            "system_prompt": "Act as a professional contract reviewer.",
            "model": "gpt-5.6-terra",
            "tools": ["bash"],
            "max_turns": 80,
        }
    )

    assert role.model_dump() == {
        "description": "Checks frontend and backend contracts.",
        "system_prompt": "Act as a professional contract reviewer.",
        "model": "gpt-5.6-terra",
    }


def test_registry_exposes_custom_role_to_the_lead():
    app_config = SubagentsAppConfig(custom_agents={"contract-reviewer": {"description": "Checks frontend and backend contracts."}})

    role = get_subagent_config("contract-reviewer", app_config=app_config)

    assert role == SubagentConfig(name="contract-reviewer", description="Checks frontend and backend contracts.")
    assert "contract-reviewer" in get_subagent_names(app_config=app_config)


@pytest.mark.parametrize("role_name", ["executor", "evaluator"])
def test_registry_allows_local_command_room_executor_and_evaluator_overrides(role_name):
    description = f"Local {role_name} role context."
    app_config = SubagentsAppConfig(custom_agents={role_name: {"description": description}})

    role = get_subagent_config(role_name, app_config=app_config)

    assert role == SubagentConfig(name=role_name, description=description)
