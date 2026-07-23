"""Subagent registry for managing available professional role labels."""

from typing import Any

from deerflow.subagents.builtins import BUILTIN_SUBAGENTS
from deerflow.subagents.config import SubagentConfig

_CUSTOM_OVERRIDABLE_BUILTINS = {
    "executor",
    "fact-finder",
    "opposition",
    "recorder",
}


def _resolve_subagents_app_config(app_config: Any | None = None):
    if app_config is None:
        from deerflow.config.subagents_config import get_subagents_app_config

        return get_subagents_app_config()
    return getattr(app_config, "subagents", app_config)


def _build_custom_subagent_config(name: str, *, app_config: Any | None = None) -> SubagentConfig | None:
    """Build professional role metadata from ``custom_agents``."""
    subagents_config = _resolve_subagents_app_config(app_config)
    custom = getattr(subagents_config, "custom_agents", {}).get(name)
    if custom is None:
        return None

    return SubagentConfig(
        name=name,
        description=custom.description,
        system_prompt=custom.system_prompt,
    )


def get_subagent_config(name: str, *, app_config: Any | None = None) -> SubagentConfig | None:
    """Get professional role metadata by name.

    Resolution order:
    1. Built-in subagents (general-purpose, bash)
    2. Custom subagents from config.yaml custom_agents section

    Args:
        name: The name of the subagent.
        app_config: Optional AppConfig or SubagentsAppConfig to resolve overrides from.

    Returns:
        SubagentConfig if found, None otherwise.
    """
    # Step 1: Look up built-in, then fall back to custom_agents. Allow selected
    # Command Room roles to keep local custom definitions when present.
    if name in _CUSTOM_OVERRIDABLE_BUILTINS:
        config = _build_custom_subagent_config(name, app_config=app_config)
        if config is None:
            config = BUILTIN_SUBAGENTS.get(name)
    else:
        config = BUILTIN_SUBAGENTS.get(name)
        if config is None:
            config = _build_custom_subagent_config(name, app_config=app_config)
    return config


def list_subagents(*, app_config: Any | None = None) -> list[SubagentConfig]:
    """List all available professional roles.

    Returns:
        List of all registered SubagentConfig instances (built-in + custom).
    """
    configs = []
    for name in get_subagent_names(app_config=app_config):
        config = get_subagent_config(name, app_config=app_config)
        if config is not None:
            configs.append(config)
    return configs


def get_subagent_names(*, app_config: Any | None = None) -> list[str]:
    """Get all available subagent names (built-in + custom).

    Returns:
        List of subagent names.
    """
    names = list(BUILTIN_SUBAGENTS.keys())

    # Merge custom_agents from config.yaml
    subagents_config = _resolve_subagents_app_config(app_config)
    for custom_name in getattr(subagents_config, "custom_agents", {}):
        if custom_name not in names:
            names.append(custom_name)

    return names


def get_available_subagent_names(*, app_config: Any | None = None) -> list[str]:
    """Get professional role labels exposed to the lead AI.

    Returns:
        List of role labels. Labels do not grant or restrict Codex CLI tools.
    """
    return get_subagent_names(app_config=app_config)
