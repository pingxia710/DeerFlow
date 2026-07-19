"""Tests for one-shot role labels and prompt exposure."""

from deerflow.agents.lead_agent import prompt as prompt_module
from deerflow.subagents import registry as registry_module


def test_role_labels_do_not_try_to_control_codex_cli_tools() -> None:
    names = registry_module.get_available_subagent_names()

    assert "general-purpose" in names
    assert "bash" in names
    bash_role = registry_module.get_subagent_config("bash")
    assert bash_role is not None
    assert not hasattr(bash_role, "tools")


def test_build_subagent_section_hides_bash_examples_when_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(prompt_module, "get_available_subagent_names", lambda: ["general-purpose"])

    section = prompt_module._build_subagent_section()

    assert "**bash**" not in section
    assert "prompt is the complete AI-AI contract" in section
    assert "returns its complete natural-language result and then ends" in section


def test_build_subagent_section_includes_bash_when_available(monkeypatch) -> None:
    monkeypatch.setattr(prompt_module, "get_available_subagent_names", lambda: ["general-purpose", "bash"])

    section = prompt_module._build_subagent_section()

    assert "For command execution (git, build, test, deploy operations)" in section
    assert "No program-defined task-call count" in section
    assert "maximum" not in section
