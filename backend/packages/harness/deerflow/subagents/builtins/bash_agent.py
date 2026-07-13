"""Bash command execution subagent configuration."""

from deerflow.subagents.config import SubagentConfig

BASH_AGENT_CONFIG = SubagentConfig(
    name="bash",
    description="A one-shot command execution AI for shell work, builds, tests, diagnostics, and verbose terminal output.",
)
