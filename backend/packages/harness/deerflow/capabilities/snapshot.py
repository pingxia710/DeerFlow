"""Build AI-readable capability snapshots from current DeerFlow facts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from deerflow.config.app_config import AppConfig
from deerflow.runtime.user_context import DEFAULT_USER_ID

from .profile import (
    AgentHarnessProfile,
    AsyncTaskPolicy,
    FilesystemPermission,
    MiddlewareCapability,
    ProtectedScaffolding,
    SkillCapability,
    ToolCapability,
)

_MASK = "***"
_SENSITIVE_KEY_PARTS = (
    "authorization",
    "client_secret",
    "cookie",
    "credential",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
    "api_key",
    "apikey",
)
_MASK_ALL_VALUE_KEYS = {"env", "environment", "headers"}
_PROTECTED_MIDDLEWARES = {
    "InputSanitizationMiddleware",
    "SandboxMiddleware",
    "GuardrailMiddleware",
    "SandboxAuditMiddleware",
    "ToolErrorHandlingMiddleware",
    "CommandRoomRoundContextMiddleware",
    "TokenBudgetMiddleware",
    "SafetyFinishReasonMiddleware",
}
_DEFAULT_STOP_BEFORE = [
    "live business or production writes",
    "credential or raw sensitive-data disclosure",
    "payment, refund, withdrawal, or financial actions",
    "public/customer-facing behavior changes",
]


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _mask_secrets(value: Any, *, parent_key: str = "") -> Any:
    if isinstance(value, Mapping):
        masked: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                masked[key_text] = _MASK if item not in (None, "") else item
            elif key_text.lower() in _MASK_ALL_VALUE_KEYS and isinstance(item, Mapping):
                masked[key_text] = {str(k): _MASK for k in item}
            else:
                masked[key_text] = _mask_secrets(item, parent_key=key_text)
        return masked
    if isinstance(value, list):
        return [_mask_secrets(item, parent_key=parent_key) for item in value]
    if parent_key.lower() in _MASK_ALL_VALUE_KEYS and value not in (None, ""):
        return _MASK
    return value


def _tool_risk(name: str, group: str | None = None, use: str | None = None) -> str:
    text = " ".join(part for part in (name, group or "", use or "") if part).lower()
    if any(marker in text for marker in ("bash", "shell", "write", "replace", "update_agent", "setup_agent", "execute")):
        return "high"
    if any(marker in text for marker in ("task", "mcp", "invoke", "tool_search")):
        return "medium"
    return "low"


def _tool_read_only(name: str, group: str | None = None, use: str | None = None) -> bool | None:
    risk = _tool_risk(name, group, use)
    if risk == "high":
        return False
    lowered = name.lower()
    if any(marker in lowered for marker in ("read", "ls", "list", "grep", "glob", "view", "present", "ask")):
        return True
    return None


def _tool_capability(name: str, source: str, *, group: str | None = None, use: str | None = None) -> ToolCapability:
    risk = _tool_risk(name, group, use)
    return ToolCapability(
        name=name,
        source=source,
        risk_level=risk,  # mechanical label only; the AI still decides how to use it
        read_only=_tool_read_only(name, group, use),
        requires_approval=risk == "high",
    )


def _model_facts(app_config: AppConfig) -> list[dict[str, Any]]:
    known = {
        "name",
        "display_name",
        "description",
        "use",
        "model",
        "use_responses_api",
        "output_version",
        "supports_thinking",
        "supports_reasoning_effort",
        "reasoning_efforts",
        "default_reasoning_effort",
        "supports_vision",
        "stream_chunk_timeout",
        "subagents_inherit",
    }
    facts: list[dict[str, Any]] = []
    for model in app_config.models:
        raw = _mask_secrets(model.model_dump())
        fact = {key: raw.get(key) for key in known if key in raw}
        extra = {key: value for key, value in raw.items() if key not in known and value is not None}
        if extra:
            fact["extra"] = extra
        facts.append(fact)
    return facts


def _current_model_route(app_config: AppConfig) -> dict[str, Any]:
    default_model = app_config.models[0].name if app_config.models else None
    return {
        "default_model": default_model,
        "source": "config.models[0]",
        "thinking_default": True,
    }


def _configured_tools(app_config: AppConfig) -> list[ToolCapability]:
    tools = [_tool_capability(tool.name, "config.tools", group=tool.group, use=tool.use) for tool in app_config.tools]
    for name in ("present_files", "ask_clarification"):
        tools.append(_tool_capability(name, "deerflow.tools.builtins"))
    if app_config.models and any(model.supports_vision for model in app_config.models):
        tools.append(_tool_capability("view_image", "deerflow.tools.builtins"))
    tools.append(_tool_capability("task", "deerflow.subagents"))
    if app_config.tool_search.enabled:
        tools.append(_tool_capability("tool_search", "deerflow.tools.builtins.tool_search"))

    by_name: dict[str, ToolCapability] = {}
    for tool in tools:
        by_name.setdefault(tool.name, tool)
    return list(by_name.values())


def _skill_facts(app_config: AppConfig) -> tuple[list[dict[str, Any]], list[SkillCapability]]:
    try:
        from deerflow.skills.storage import get_or_new_skill_storage

        skills = get_or_new_skill_storage(app_config=app_config).load_skills(enabled_only=False)
    except Exception as exc:  # noqa: BLE001 - snapshot should expose the load failure as a fact
        return (
            [
                {
                    "source": "skill_storage",
                    "available": False,
                    "error": f"{exc.__class__.__name__}: {exc}",
                }
            ],
            [],
        )

    facts: list[dict[str, Any]] = []
    profile_skills: list[SkillCapability] = []
    for skill in skills:
        category = str(skill.category)
        enabled = bool(getattr(skill, "enabled", True))
        facts.append(
            {
                "name": skill.name,
                "description": skill.description,
                "license": skill.license,
                "category": category,
                "enabled": enabled,
                "skill_path": skill.skill_path,
                "container_path": skill.get_container_file_path(),
                "source": "skill_storage",
            }
        )
        profile_skills.append(SkillCapability(name=skill.name, source=category, enabled=enabled))
    return facts, profile_skills


def _mcp_server_facts(app_config: AppConfig) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for name, server in app_config.extensions.mcp_servers.items():
        raw = _mask_secrets(server.model_dump())
        raw["name"] = name
        raw["source"] = "extensions_config"
        raw["risk_level"] = "medium" if server.enabled else "low"
        raw["read_only"] = None
        raw["requires_approval"] = False
        facts.append(raw)
    return facts


def _skill_catalog_source_facts(app_config: AppConfig) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for name, source in app_config.extensions.skill_catalog_sources.items():
        raw = _mask_secrets(source.model_dump(by_alias=True))
        raw["name"] = name
        raw["source"] = "extensions_config.skillCatalogSources"
        raw["requires_approval"] = source.trust_level == "community"
        facts.append(raw)
    return facts


def _subagent_facts(app_config: AppConfig) -> list[dict[str, Any]]:
    from deerflow.subagents.builtins import BUILTIN_SUBAGENTS
    from deerflow.subagents.registry import get_available_subagent_names, list_subagents

    available = set(get_available_subagent_names(app_config=app_config))
    facts: list[dict[str, Any]] = []
    for subagent in list_subagents(app_config=app_config):
        facts.append(
            {
                "name": subagent.name,
                "description": subagent.description,
                "available": subagent.name in available,
                "source": "built-in" if subagent.name in BUILTIN_SUBAGENTS else "config.subagents.custom_agents",
                "tools": subagent.tools,
                "disallowed_tools": subagent.disallowed_tools,
                "skills": subagent.skills,
                "model": subagent.model,
                "max_turns": subagent.max_turns,
                "timeout_seconds": subagent.timeout_seconds,
                "system_prompt_available": bool(subagent.system_prompt),
            }
        )
    return facts


def _command_room_runtime_facts(
    app_config: AppConfig,
    *,
    user_id: str,
    skills: list[dict[str, Any]],
) -> dict[str, Any]:
    """Describe the Command Room's actual direct/delegated routing contract."""
    from deerflow.agents.lead_agent.agent import (
        _resolve_agent_tool_groups,
        _resolve_command_room_available_skills,
        _resolve_subagent_available_skills,
        _resolve_subagent_tool_groups,
    )
    from deerflow.config.agents_config import load_agent_config
    from deerflow.mcp.cache import get_mcp_cache_status
    from deerflow.subagents.builtins.command_room_roles import COMMAND_ROOM_ROLE_CONFIGS

    config_status = "loaded"
    config_error_type: str | None = None
    try:
        agent_config = load_agent_config("command-room", user_id=user_id)
    except Exception as exc:  # noqa: BLE001 - expose only the failure type
        agent_config = None
        config_status = "unavailable"
        config_error_type = exc.__class__.__name__

    default_model = app_config.models[0].name if app_config.models else None
    requested_model = agent_config.model if agent_config is not None else None
    resolved_model = requested_model if requested_model and app_config.get_model_config(requested_model) else default_model
    model_fallback = bool(requested_model and requested_model != resolved_model)

    all_skill_names = {str(item.get("name")) for item in skills if item.get("name")}
    enabled_skill_names = {str(item.get("name")) for item in skills if item.get("name") and item.get("enabled") is True}
    configured_skills = agent_config.skills if agent_config is not None else []
    if configured_skills is None:
        direct_skill_names = sorted(enabled_skill_names)
        delegated_allowlist: set[str] | None = None
    else:
        lead_allowlist = _resolve_command_room_available_skills("command-room", set(configured_skills)) or set()
        direct_skill_names = sorted(lead_allowlist & enabled_skill_names)
        delegated_allowlist = _resolve_subagent_available_skills("command-room", set(configured_skills))

    missing_skills = sorted(set(configured_skills or []) - all_skill_names)
    disabled_skills = sorted((set(configured_skills or []) & all_skill_names) - enabled_skill_names)
    role_skill_names = sorted({skill for config in COMMAND_ROOM_ROLE_CONFIGS.values() for skill in (config.skills or [])})
    delegated_loaded_skills = sorted(enabled_skill_names if delegated_allowlist is None else enabled_skill_names & delegated_allowlist)

    direct_groups = _resolve_agent_tool_groups("command-room", agent_config)
    delegated_groups = _resolve_subagent_tool_groups("command-room", agent_config)
    direct_tools = [tool.name for tool in app_config.tools if direct_groups is None or tool.group in direct_groups]
    direct_tools.extend(["present_files", "ask_clarification", "task"])
    resolved_model_config = app_config.get_model_config(resolved_model) if resolved_model else None
    if resolved_model_config is not None and resolved_model_config.supports_vision:
        direct_tools.append("view_image")

    enabled_mcp_servers = sorted(name for name, server in app_config.extensions.mcp_servers.items() if server.enabled)
    delegated_tools = [tool.name for tool in app_config.tools]
    delegated_tools.extend(["present_files", "ask_clarification"])
    if resolved_model_config is not None and resolved_model_config.supports_vision:
        delegated_tools.append("view_image")

    return {
        "agent_config": {
            "status": config_status,
            "error_type": config_error_type,
            "requested_model": requested_model,
            "resolved_model": resolved_model,
            "model_fallback": model_fallback,
        },
        "skills": {
            "configured": configured_skills,
            "loaded": direct_skill_names,
            "missing": missing_skills,
            "disabled": disabled_skills,
            "delegated_loaded": delegated_loaded_skills,
            "role_skills": role_skill_names,
        },
        "direct": {
            "tool_groups": direct_groups,
            "configured_tools": sorted(set(direct_tools)),
            "include_mcp": False,
            "mcp_access": "delegated_only" if enabled_mcp_servers else "not_configured",
        },
        "delegated": {
            "tool_groups": delegated_groups,
            "configured_tools": sorted(set(delegated_tools)),
            "include_mcp": True,
            "mcp_servers_configured": enabled_mcp_servers,
            "mcp_cache": get_mcp_cache_status(),
        },
    }


def _sandbox_facts(app_config: AppConfig) -> dict[str, Any]:
    from deerflow.sandbox.security import is_host_bash_allowed, is_unrestricted_host_access_allowed, uses_local_sandbox_provider

    sandbox = app_config.sandbox
    return _mask_secrets(
        {
            "use": sandbox.use,
            "uses_local_provider": uses_local_sandbox_provider(app_config),
            "allow_host_bash": sandbox.allow_host_bash,
            "host_bash_available": is_host_bash_allowed(app_config),
            "default_cwd": sandbox.default_cwd,
            "unrestricted_host_access": sandbox.unrestricted_host_access,
            "unrestricted_host_access_available": is_unrestricted_host_access_allowed(app_config),
            "image": sandbox.image,
            "port": sandbox.port,
            "replicas": sandbox.replicas,
            "container_prefix": sandbox.container_prefix,
            "idle_timeout": sandbox.idle_timeout,
            "mounts": [mount.model_dump() for mount in sandbox.mounts],
            "allow_dangerous_host_mounts": sandbox.allow_dangerous_host_mounts,
            "environment": sandbox.environment,
            "seccomp_unconfined": sandbox.seccomp_unconfined,
            "bash_output_max_chars": sandbox.bash_output_max_chars,
            "read_file_output_max_chars": sandbox.read_file_output_max_chars,
            "ls_output_max_chars": sandbox.ls_output_max_chars,
        }
    )


def _filesystem_permissions(app_config: AppConfig) -> list[FilesystemPermission]:
    sandbox = app_config.sandbox
    return [
        FilesystemPermission(label="read", scope="thread user-data workspace/uploads/outputs", source="ThreadDataMiddleware"),
        FilesystemPermission(label="write", scope="thread user-data workspace/outputs", source="sandbox file tools"),
        FilesystemPermission(label="execute", scope="sandbox bash tool", enabled=bool(_sandbox_facts(app_config)["host_bash_available"]), source="sandbox config"),
        FilesystemPermission(
            label="denied",
            scope="arbitrary host paths",
            enabled=not bool(sandbox.unrestricted_host_access),
            source="sandbox.unrestricted_host_access",
        ),
        FilesystemPermission(
            label="approval_required",
            scope="dangerous host mounts",
            enabled=not bool(sandbox.allow_dangerous_host_mounts),
            source="sandbox.allow_dangerous_host_mounts",
        ),
    ]


def _middleware_stack(app_config: AppConfig) -> list[MiddlewareCapability]:
    try:
        from deerflow.agents.lead_agent.agent import build_middlewares

        model_name = app_config.models[0].name if app_config.models else None
        middlewares = build_middlewares(
            {"configurable": {"subagent_enabled": True}},
            model_name=model_name,
            agent_name="command-room",
            app_config=app_config,
        )
        names = [middleware.__class__.__name__ for middleware in middlewares]
    except Exception as exc:  # noqa: BLE001 - expose degraded fact, do not fail the snapshot
        names = [f"unavailable:{exc.__class__.__name__}"]
    return [
        MiddlewareCapability(
            name=name,
            source="lead_agent.build_middlewares",
            protected=name in _PROTECTED_MIDDLEWARES,
        )
        for name in names
    ]


def _protected_scaffolding(middleware_stack: list[MiddlewareCapability]) -> list[ProtectedScaffolding]:
    return [ProtectedScaffolding(name=item.name, reason="core safety/context middleware") for item in middleware_stack if item.protected]


def _approval_policy_facts(app_config: AppConfig) -> dict[str, Any]:
    guardrails = app_config.guardrails
    provider = getattr(guardrails, "provider", None)
    return {
        "source": "current DeerFlow configuration and project safety policy",
        "program_makes_next_step_decisions": False,
        "guardrails_enabled": bool(guardrails.enabled),
        "guardrail_provider": getattr(provider, "use", None) if provider is not None else None,
        "mcp_config_admin_required": True,
        "global_skill_management_admin_required": True,
        "thread_owner_check_required": True,
        "stop_before": list(_DEFAULT_STOP_BEFORE),
    }


def _capability_center(
    *,
    app_config: AppConfig,
    thread_id: str | None,
    tools: list[ToolCapability],
    skills: list[dict[str, Any]],
    filesystem_permissions: list[FilesystemPermission],
) -> dict[str, Any]:
    sandbox = _sandbox_facts(app_config)
    approval_policy = _approval_policy_facts(app_config)
    return {
        "current_release": {
            "source": "current request configuration",
            "thread_scoped": thread_id is not None,
            "tools": [tool.model_dump() for tool in tools],
            "subagents_available": bool(_subagent_facts(app_config)),
            "skills": [
                {
                    "name": skill.get("name"),
                    "enabled": skill.get("enabled"),
                    "category": skill.get("category"),
                    "source": "skill_storage",
                }
                for skill in skills
            ],
            "filesystem": [item.model_dump() for item in filesystem_permissions],
            "sandbox": {
                "use": sandbox.get("use"),
                "uses_local_provider": sandbox.get("uses_local_provider"),
                "allow_host_bash": sandbox.get("allow_host_bash"),
                "host_bash_available": sandbox.get("host_bash_available"),
                "unrestricted_host_access": sandbox.get("unrestricted_host_access"),
                "unrestricted_host_access_available": sandbox.get("unrestricted_host_access_available"),
            },
            "middleware_source": "lead_agent.build_middlewares",
            "program_decides_next_step": False,
        },
        "stop_before": list(approval_policy["stop_before"]),
        "permission_facts": {
            "filesystem_scope": [item.model_dump() for item in filesystem_permissions],
            "sandbox_unrestricted_host_access": sandbox.get("unrestricted_host_access"),
            "sandbox_unrestricted_host_access_available": sandbox.get("unrestricted_host_access_available"),
            "sandbox_allow_host_bash": sandbox.get("allow_host_bash"),
            "sandbox_host_bash_available": sandbox.get("host_bash_available"),
            "approval_requirements": {
                "mcp_config_admin_required": approval_policy["mcp_config_admin_required"],
                "global_skill_management_admin_required": approval_policy["global_skill_management_admin_required"],
                "thread_owner_check_required": approval_policy["thread_owner_check_required"],
            },
            "program_makes_next_step_decisions": False,
        },
        "evidence_refs": [
            "deerflow.capabilities.snapshot",
            "config.tools",
            "config.sandbox",
            "skill_storage",
            "lead_agent.build_middlewares",
        ],
        "advisory_only": True,
        "non_decisions": {
            "program_makes_next_step_decisions": False,
            "auto_authorize": False,
            "auto_reject": False,
            "auto_pass_fail": False,
            "auto_dispatch": False,
            "auto_rework": False,
        },
    }


def _risk_notes(app_config: AppConfig) -> list[dict[str, Any]]:
    sandbox = app_config.sandbox
    return [
        {"name": "sandbox.allow_host_bash", "enabled": bool(sandbox.allow_host_bash)},
        {"name": "sandbox.unrestricted_host_access", "enabled": bool(sandbox.unrestricted_host_access)},
        {"name": "sandbox.allow_dangerous_host_mounts", "enabled": bool(sandbox.allow_dangerous_host_mounts)},
        {"name": "sandbox.seccomp_unconfined", "enabled": bool(sandbox.seccomp_unconfined)},
    ]


def _profile(
    *,
    agent_name: str,
    role: str,
    tools: list[ToolCapability],
    skills: list[SkillCapability],
    middleware_stack: list[MiddlewareCapability],
    filesystem_permissions: list[FilesystemPermission],
) -> AgentHarnessProfile:
    return AgentHarnessProfile(
        agent_name=agent_name,
        role=role,
        tools=tools,
        skills=skills,
        memory_sources=["user", "agent", "thread"],
        middleware_stack=middleware_stack,
        filesystem_permissions=filesystem_permissions,
        async_task_policy=AsyncTaskPolicy(),
        protected_scaffolding=_protected_scaffolding(middleware_stack),
    )


def build_capability_snapshot(
    app_config: AppConfig,
    *,
    thread_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Return factual AI-readable capability state."""

    from deerflow.utils.time import now_iso

    tools = _configured_tools(app_config)
    skills, profile_skills = _skill_facts(app_config)
    effective_user_id = user_id or DEFAULT_USER_ID
    middleware_stack = _middleware_stack(app_config)
    filesystem_permissions = _filesystem_permissions(app_config)
    command_room_runtime = _command_room_runtime_facts(
        app_config,
        user_id=effective_user_id,
        skills=skills,
    )

    return {
        "version": 1,
        "user_id": effective_user_id,
        "thread_id": thread_id,
        "models": _model_facts(app_config),
        "current_model_route": _current_model_route(app_config),
        "subagents": _subagent_facts(app_config),
        "tools": [tool.model_dump() for tool in tools],
        "mcp_servers": _mcp_server_facts(app_config),
        "skills": skills,
        "skill_catalog_sources": _skill_catalog_source_facts(app_config),
        "middleware_stack": [item.model_dump() for item in middleware_stack],
        "filesystem_permissions": [item.model_dump() for item in filesystem_permissions],
        "async_tasks": [
            {
                "name": "subagent_tasks",
                **AsyncTaskPolicy().model_dump(),
                "runtime_state_available": False,
            }
        ],
        "sandbox": _sandbox_facts(app_config),
        "token_budget": app_config.token_budget.model_dump(),
        "approval_policy": _approval_policy_facts(app_config),
        "capability_release": {
            "source": "current request configuration",
            "thread_scoped": thread_id is not None,
            "program_decides_next_step": False,
        },
        "capability_center": _capability_center(
            app_config=app_config,
            thread_id=thread_id,
            tools=tools,
            skills=skills,
            filesystem_permissions=filesystem_permissions,
        ),
        "command_room_runtime": command_room_runtime,
        "risk_notes": _risk_notes(app_config),
        "agent_harness_profiles": [
            _profile(
                agent_name="command-room",
                role="chair",
                tools=tools,
                skills=profile_skills,
                middleware_stack=middleware_stack,
                filesystem_permissions=filesystem_permissions,
            ).model_dump(),
            _profile(
                agent_name="subagent",
                role="worker",
                tools=tools,
                skills=profile_skills,
                middleware_stack=middleware_stack,
                filesystem_permissions=filesystem_permissions,
            ).model_dump(),
        ],
        "updated_at": now_iso(),
    }


__all__ = ["build_capability_snapshot"]
