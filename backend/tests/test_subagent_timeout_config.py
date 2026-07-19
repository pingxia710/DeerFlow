"""Tests for one-shot Codex task transport configuration."""

import pytest

from deerflow.config.subagents_config import (
    SubagentOverrideConfig,
    SubagentsAppConfig,
    get_subagents_app_config,
    load_subagents_config_from_dict,
)


@pytest.fixture(autouse=True)
def _reset_global_subagents_config():
    load_subagents_config_from_dict({})
    yield
    load_subagents_config_from_dict({})


def test_default_transport_is_one_hour_without_forced_reasoning_effort():
    config = SubagentsAppConfig()

    assert config.timeout_seconds == 3600
    assert config.reasoning_effort is None
    assert config.model is None
    assert config.agents == {}
    assert config.custom_agents == {}


def test_transport_accepts_xhigh_and_model_label_override():
    config = SubagentsAppConfig(
        timeout_seconds=3600,
        reasoning_effort="xhigh",
        agents={
            "general-purpose": {"model": "gpt-5.6-terra"},
            "planner": {"model": "gpt-5.6", "reasoning_effort": "max"},
        },
    )

    assert config.reasoning_effort == "xhigh"
    assert config.get_model_for("general-purpose") == "gpt-5.6-terra"
    assert config.get_model_for("opposition") == "gpt-5.6-terra"
    assert config.get_model_for("planner") == "gpt-5.6"
    assert config.get_reasoning_effort_for("planner") == "max"
    assert config.get_reasoning_effort_for("executor") == "xhigh"


def test_global_model_is_default_for_every_role():
    config = SubagentsAppConfig(
        model="gpt-5.6-terra",
        agents={"opposition": {"model": "gpt-5.6-terra-opposition"}},
    )

    assert config.get_model_for("general-purpose") == "gpt-5.6-terra"
    assert config.get_model_for("implementation") == "gpt-5.6-terra"
    assert config.get_model_for("opposition") == "gpt-5.6-terra-opposition"


def test_legacy_inherit_model_uses_global_or_general_default(caplog):
    load_subagents_config_from_dict(
        {
            "model": "gpt-5.6-terra",
            "agents": {"opposition": {"model": "inherit"}},
            "custom_agents": {
                "verifier": {
                    "description": "Verify work.",
                    "model": "inherit",
                }
            },
        }
    )

    config = get_subagents_app_config()
    assert config.get_model_for("opposition") == "gpt-5.6-terra"
    assert config.get_model_for("verifier") == "gpt-5.6-terra"
    assert "Legacy model='inherit' is ignored" in caplog.text


def test_timeout_must_be_positive():
    with pytest.raises(ValueError):
        SubagentsAppConfig(timeout_seconds=0)


def test_reasoning_effort_uses_codex_name():
    with pytest.raises(ValueError):
        SubagentsAppConfig(reasoning_effort="extra-high")


def test_model_override_must_be_non_empty():
    with pytest.raises(ValueError):
        SubagentOverrideConfig(model="")


def test_removed_program_controls_are_not_part_of_the_model():
    config = SubagentsAppConfig.model_validate(
        {
            "timeout_seconds": 3600,
            "max_turns": 20,
            "process_wide_queue_size": 64,
            "agents": {
                "general-purpose": {
                    "model": "gpt-5.6-terra",
                    "tools": ["bash"],
                    "skills": ["review"],
                }
            },
        }
    )

    assert not hasattr(config, "max_turns")
    assert not hasattr(config, "process_wide_queue_size")
    assert config.agents["general-purpose"].model_dump() == {
        "model": "gpt-5.6-terra",
        "reasoning_effort": None,
    }


def test_loader_replaces_the_runtime_transport_config():
    load_subagents_config_from_dict(
        {
            "timeout_seconds": 1800,
            "reasoning_effort": "high",
            "agents": {"verifier": {"model": "gpt-5.6-terra"}},
        }
    )

    config = get_subagents_app_config()
    assert config.timeout_seconds == 1800
    assert config.reasoning_effort == "high"
    assert config.get_model_for("verifier") == "gpt-5.6-terra"


def test_loader_warns_when_legacy_program_controls_are_ignored(caplog):
    load_subagents_config_from_dict(
        {
            "process_wide_max_concurrent": 12,
            "agents": {"verifier": {"model": "gpt-5.6-terra", "max_turns": 20}},
            "custom_agents": {
                "verifier": {
                    "description": "Verify work.",
                    "system_prompt": "Act as a professional verifier.",
                    "tools": ["bash"],
                }
            },
        }
    )

    assert "Ignored legacy subagent execution fields" in caplog.text
    assert "subagents.process_wide_max_concurrent" in caplog.text
    assert "subagents.agents.verifier.max_turns" in caplog.text
    assert "subagents.custom_agents.verifier.tools" in caplog.text
