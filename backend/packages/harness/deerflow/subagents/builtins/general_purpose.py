"""General-purpose subagent configuration."""

from deerflow.subagents.config import SubagentConfig

GENERAL_PURPOSE_CONFIG = SubagentConfig(
    name="general-purpose",
    description="A one-shot general execution AI for exploration, implementation, commands, checks, and other delegated work.",
)
