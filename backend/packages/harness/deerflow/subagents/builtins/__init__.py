"""Built-in subagent configurations."""

from .bash_agent import BASH_AGENT_CONFIG
from .command_room_roles import COMMAND_ROOM_ROLE_CONFIGS
from .general_purpose import GENERAL_PURPOSE_CONFIG

__all__ = [
    "GENERAL_PURPOSE_CONFIG",
    "BASH_AGENT_CONFIG",
    "COMMAND_ROOM_ROLE_CONFIGS",
]

# Registry of built-in subagents
BUILTIN_SUBAGENTS = {
    "general-purpose": GENERAL_PURPOSE_CONFIG,
    "bash": BASH_AGENT_CONFIG,
    **COMMAND_ROOM_ROLE_CONFIGS,
}
