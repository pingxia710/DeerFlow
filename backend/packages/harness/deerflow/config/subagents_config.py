"""Configuration for the subagent system loaded from config.yaml."""

import logging
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field, model_validator

from deerflow.config.model_config import ReasoningEffort

logger = logging.getLogger(__name__)


class SubagentOverrideConfig(BaseModel):
    """Optional model settings for a task label."""

    model: str | None = Field(
        default=None,
        min_length=1,
        description="Model name for this task label (None = use the Codex CLI default)",
    )
    reasoning_effort: ReasoningEffort | None = Field(
        default=None,
        description="Optional reasoning effort for this task label",
    )


class CustomSubagentConfig(BaseModel):
    """User-defined professional role exposed to the lead AI."""

    description: str = Field(
        description="When the lead agent should delegate to this subagent",
    )
    system_prompt: str | None = Field(
        default=None,
        description="Developer-authored professional role context included in the one-shot Codex prompt",
    )
    model: str | None = Field(
        default=None,
        min_length=1,
        description="Optional Codex model for this professional role",
    )


class SubagentsAppConfig(BaseModel):
    """Configuration for the subagent system."""

    timeout_seconds: int = Field(
        default=3600,
        ge=1,
        description="Default timeout in seconds for delegated Codex CLI tasks (default: 3600 = 60 minutes)",
    )
    reasoning_effort: ReasoningEffort | None = Field(
        default=None,
        description="Optional reasoning effort passed to every delegated Codex CLI task",
    )
    model: str | None = Field(
        default=None,
        min_length=1,
        description="Default Codex model for delegated tasks",
    )
    agents: dict[str, SubagentOverrideConfig] = Field(
        default_factory=dict,
        description="Optional model and reasoning-effort overrides keyed by task label",
    )
    custom_agents: dict[str, CustomSubagentConfig] = Field(
        default_factory=dict,
        description="Additional professional role names and descriptions for the lead AI",
    )

    @model_validator(mode="before")
    @classmethod
    def warn_and_remove_legacy_execution_fields(cls, value: Any) -> Any:
        """Keep old config readable while making ignored execution policy visible."""
        if not isinstance(value, Mapping):
            return value

        normalized = dict(value)
        ignored_fields: list[str] = []
        legacy_inherit_fields: list[str] = []
        allowed_top_level = {"timeout_seconds", "reasoning_effort", "model", "agents", "custom_agents"}
        ignored_fields.extend(f"subagents.{key}" for key in normalized if key not in allowed_top_level)

        agents = normalized.get("agents")
        if isinstance(agents, Mapping):
            normalized_agents: dict[str, Any] = {}
            for name, raw_config in agents.items():
                if not isinstance(raw_config, Mapping):
                    normalized_agents[str(name)] = raw_config
                    continue
                agent_config = dict(raw_config)
                ignored_fields.extend(f"subagents.agents.{name}.{key}" for key in agent_config if key not in {"model", "reasoning_effort"})
                if agent_config.get("model") == "inherit":
                    legacy_inherit_fields.append(f"subagents.agents.{name}.model")
                    agent_config["model"] = None
                normalized_agents[str(name)] = agent_config
            normalized["agents"] = normalized_agents

        custom_agents = normalized.get("custom_agents")
        if isinstance(custom_agents, Mapping):
            normalized_custom_agents: dict[str, Any] = {}
            for name, raw_config in custom_agents.items():
                if not isinstance(raw_config, Mapping):
                    normalized_custom_agents[str(name)] = raw_config
                    continue
                custom_config = dict(raw_config)
                ignored_fields.extend(f"subagents.custom_agents.{name}.{key}" for key in custom_config if key not in {"description", "system_prompt", "model"})
                if custom_config.get("model") == "inherit":
                    legacy_inherit_fields.append(f"subagents.custom_agents.{name}.model")
                    custom_config["model"] = None
                normalized_custom_agents[str(name)] = custom_config
            normalized["custom_agents"] = normalized_custom_agents

        if normalized.get("model") == "inherit":
            legacy_inherit_fields.append("subagents.model")
            normalized["model"] = None

        if ignored_fields:
            logger.warning(
                "Ignored legacy subagent execution fields: %s. DeerFlow no longer applies tool lists, "
                "skill lists, turn limits, per-role timeouts, queues, or programmatic execution policies; "
                "put natural-language role context in system_prompt and task-specific context in the task prompt.",
                ", ".join(sorted(ignored_fields)),
            )
        if legacy_inherit_fields:
            logger.warning(
                "Legacy model='inherit' is ignored for %s. One-shot Codex tasks use the configured subagents.model, general-purpose fallback, or Codex CLI default; they do not inherit the lead model.",
                ", ".join(sorted(legacy_inherit_fields)),
            )
        return normalized

    def get_model_for(self, agent_name: str) -> str | None:
        """Get the model override for a specific agent.

        Args:
            agent_name: The name of the subagent.

        Returns:
            Configured Codex model name, or None to use the Codex CLI default.
        """
        override = self.agents.get(agent_name)
        if override is not None and override.model not in {None, "inherit"}:
            return override.model
        custom = self.custom_agents.get(agent_name)
        if custom is not None and custom.model not in {None, "inherit"}:
            return custom.model
        if self.model not in {None, "inherit"}:
            return self.model
        general_override = self.agents.get("general-purpose")
        if general_override is not None and general_override.model not in {None, "inherit"}:
            return general_override.model
        return None

    def get_reasoning_effort_for(self, agent_name: str) -> ReasoningEffort | None:
        """Get the configured reasoning effort for a task label."""
        override = self.agents.get(agent_name)
        if override is not None and override.reasoning_effort is not None:
            return override.reasoning_effort
        if self.reasoning_effort is not None:
            return self.reasoning_effort
        general_override = self.agents.get("general-purpose")
        if general_override is not None:
            return general_override.reasoning_effort
        return None


_subagents_config: SubagentsAppConfig = SubagentsAppConfig()


def get_subagents_app_config() -> SubagentsAppConfig:
    """Get the current subagents configuration."""
    return _subagents_config


def load_subagents_config_from_dict(config_dict: dict) -> None:
    """Load subagents configuration from a dictionary."""
    global _subagents_config
    _subagents_config = SubagentsAppConfig(**config_dict)

    model_overrides = {name: override.model for name, override in _subagents_config.agents.items() if override.model is not None}
    custom_role_names = list(_subagents_config.custom_agents)

    if _subagents_config.model is not None or _subagents_config.reasoning_effort is not None or model_overrides or custom_role_names:
        logger.info(
            "Subagents config loaded: timeout=%ss, model=%s, reasoning_effort=%s, model_overrides=%s, custom_roles=%s",
            _subagents_config.timeout_seconds,
            _subagents_config.model,
            _subagents_config.reasoning_effort,
            model_overrides or "none",
            custom_role_names or "none",
        )
