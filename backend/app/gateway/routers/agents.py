"""CRUD API for custom agents."""

import asyncio
import logging
import re
import shutil
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.gateway.deps import get_config, require_admin_user
from app.gateway.path_utils import get_request_storage_user_id
from deerflow.config.agents_api_config import get_agents_api_config
from deerflow.config.agents_config import AgentConfig, list_custom_agents, load_agent_config, load_agent_soul
from deerflow.config.app_config import AppConfig
from deerflow.config.model_config import ReasoningEffort
from deerflow.config.paths import get_paths
from deerflow.config.role_assignments import RoleAssignment, RoleAssignments, load_role_assignments, update_role_assignment
from deerflow.subagents.builtins.command_room_roles import COMMAND_ROOM_ROLE_CONFIGS, COMMAND_ROOM_ROLE_SKILLS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["agents"])

AGENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")


class AgentResponse(BaseModel):
    """Response model for a custom agent."""

    name: str = Field(..., description="Agent name (hyphen-case)")
    description: str = Field(default="", description="Agent description")
    model: str | None = Field(default=None, description="Optional model override")
    reasoning_effort: ReasoningEffort | None = Field(default=None, description="Optional reasoning effort override")
    tool_groups: list[str] | None = Field(default=None, description="Optional tool group whitelist")
    skills: list[str] | None = Field(default=None, description="Optional skill whitelist (None=all, []=none)")
    soul: str | None = Field(default=None, description="SOUL.md content")
    system: bool = Field(default=False, description="Whether this is a built-in system agent")


class AgentsListResponse(BaseModel):
    """Response model for listing all custom agents."""

    agents: list[AgentResponse]


class AgentCreateRequest(BaseModel):
    """Request body for creating a custom agent."""

    name: str = Field(..., description="Agent name (must match ^[A-Za-z0-9-]+$, stored as lowercase)")
    description: str = Field(default="", description="Agent description")
    model: str | None = Field(default=None, description="Optional model override")
    reasoning_effort: ReasoningEffort | None = Field(default=None, description="Optional reasoning effort override")
    tool_groups: list[str] | None = Field(default=None, description="Optional tool group whitelist")
    skills: list[str] | None = Field(default=None, description="Optional skill whitelist (None=all enabled, []=none)")
    soul: str = Field(default="", description="SOUL.md content — agent personality and behavioral guardrails")


class AgentUpdateRequest(BaseModel):
    """Request body for updating a custom agent."""

    description: str | None = Field(default=None, description="Updated description")
    model: str | None = Field(default=None, description="Updated model override")
    reasoning_effort: ReasoningEffort | None = Field(default=None, description="Updated reasoning effort override")
    tool_groups: list[str] | None = Field(default=None, description="Updated tool group whitelist")
    skills: list[str] | None = Field(default=None, description="Updated skill whitelist (None=all, []=none)")
    soul: str | None = Field(default=None, description="Updated SOUL.md content")


class RoleResponse(BaseModel):
    """Professional role configuration exposed to the AI Team page."""

    name: str
    description: str
    skill: str
    model: str | None = None
    reasoning_effort: ReasoningEffort | None = None


class RolesListResponse(BaseModel):
    roles: list[RoleResponse]


class RoleUpdateRequest(BaseModel):
    model: str = Field(min_length=1)
    reasoning_effort: ReasoningEffort | None = None


def _validate_agent_name(name: str) -> None:
    """Validate agent name against allowed pattern.

    Args:
        name: The agent name to validate.

    Raises:
        HTTPException: 422 if the name is invalid.
    """
    if not AGENT_NAME_PATTERN.match(name):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid agent name '{name}'. Must match ^[A-Za-z0-9-]+$ (letters, digits, and hyphens only).",
        )


def _normalize_agent_name(name: str) -> str:
    """Normalize agent name to lowercase for filesystem storage."""
    return name.lower()


def _path_pair_exists(first: Path, second: Path) -> tuple[bool, bool]:
    """Check two paths without blocking the event loop caller."""
    return first.exists(), second.exists()


def _read_optional_text(path: Path) -> str | None:
    """Read and trim a text file when it exists."""
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip()


def _load_agent_response(name: str, user_id: str) -> AgentResponse:
    """Load one agent and its SOUL.md as a single blocking operation."""
    return _agent_config_to_response(
        load_agent_config(name, user_id=user_id),
        include_soul=True,
        user_id=user_id,
    )


def _require_agents_api_enabled() -> None:
    """Reject access unless the custom-agent management API is explicitly enabled."""
    if not get_agents_api_config().enabled:
        raise HTTPException(
            status_code=403,
            detail=("Custom-agent management API is disabled. Set agents_api.enabled=true to expose agent and user-profile routes over HTTP."),
        )


def _resolve_agent_user_id(request: Request) -> str:
    return get_request_storage_user_id(request)


def _agent_config_to_response(agent_cfg: AgentConfig, include_soul: bool = False, *, user_id: str | None = None) -> AgentResponse:
    """Convert AgentConfig to AgentResponse."""
    soul: str | None = None
    if include_soul:
        soul = load_agent_soul(agent_cfg.name, user_id=user_id) or ""

    return AgentResponse(
        name=agent_cfg.name,
        description=agent_cfg.description,
        model=agent_cfg.model,
        reasoning_effort=agent_cfg.reasoning_effort,
        tool_groups=agent_cfg.tool_groups,
        skills=agent_cfg.skills,
        soul=soul,
        system=agent_cfg.name == "command-room",
    )


def _role_to_response(name: str, config: AppConfig, assignments: RoleAssignments) -> RoleResponse:
    assignment = assignments.roles.get(name)
    model = assignment.model if assignment is not None else config.subagents.get_model_for(name)
    reasoning_effort = assignment.reasoning_effort if assignment is not None else config.subagents.get_reasoning_effort_for(name)
    role_config = COMMAND_ROOM_ROLE_CONFIGS[name]
    return RoleResponse(
        name=name,
        description=role_config.description,
        skill=COMMAND_ROOM_ROLE_SKILLS[name],
        model=model,
        reasoning_effort=reasoning_effort,
    )


def _validate_role_model(request: RoleUpdateRequest, config: AppConfig) -> None:
    model = config.get_model_config(request.model)
    if model is None:
        raise HTTPException(status_code=422, detail=f"Unknown model '{request.model}'")
    if request.reasoning_effort is None:
        return
    if not model.supports_reasoning_effort:
        raise HTTPException(status_code=422, detail=f"Model '{request.model}' does not support reasoning effort")
    allowed_efforts = model.reasoning_efforts or ["medium", "high", "xhigh"]
    if request.reasoning_effort not in allowed_efforts:
        raise HTTPException(
            status_code=422,
            detail=f"Reasoning effort '{request.reasoning_effort}' is not supported by model '{request.model}'",
        )


@router.get(
    "/agents",
    response_model=AgentsListResponse,
    summary="List Custom Agents",
    description="List all custom agents available in the agents directory, including their soul content.",
)
async def list_agents(request: Request) -> AgentsListResponse:
    """List all custom agents.

    Returns:
        List of all custom agents with their metadata and soul content.
    """
    _require_agents_api_enabled()

    user_id = _resolve_agent_user_id(request)

    def _load_agent_responses() -> AgentsListResponse:
        agents = list_custom_agents(user_id=user_id)
        if not any(agent.name == "command-room" for agent in agents):
            command_room = load_agent_config("command-room", user_id=user_id)
            if command_room is not None:
                agents.insert(0, command_room)
        else:
            agents.sort(key=lambda agent: (agent.name != "command-room", agent.name))
        return AgentsListResponse(agents=[_agent_config_to_response(agent, include_soul=True, user_id=user_id) for agent in agents])

    try:
        return await asyncio.to_thread(_load_agent_responses)
    except Exception as e:
        logger.error(f"Failed to list agents: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list agents: {str(e)}")


@router.get(
    "/agents/check",
    summary="Check Agent Name",
    description="Validate an agent name and check if it is available (case-insensitive).",
)
async def check_agent_name(name: str, request: Request) -> dict:
    """Check whether an agent name is valid and not yet taken.

    Args:
        name: The agent name to check.

    Returns:
        ``{"available": true/false, "name": "<normalized>"}``

    Raises:
        HTTPException: 422 if the name is invalid.
    """
    _require_agents_api_enabled()
    _validate_agent_name(name)
    normalized = _normalize_agent_name(name)
    user_id = _resolve_agent_user_id(request)
    paths = get_paths()
    # Treat the name as taken if either the per-user path or the legacy shared
    # path holds an agent — picking a name that collides with an unmigrated
    # legacy agent would shadow the legacy entry once migration runs.
    user_exists, legacy_exists = await asyncio.to_thread(
        _path_pair_exists,
        paths.user_agent_dir(user_id, normalized),
        paths.agent_dir(normalized),
    )
    available = normalized != "command-room" and not user_exists and not legacy_exists
    return {"available": available, "name": normalized}


@router.get(
    "/agents/{name}",
    response_model=AgentResponse,
    summary="Get Custom Agent",
    description="Retrieve details and SOUL.md content for a specific custom agent.",
)
async def get_agent(name: str, request: Request) -> AgentResponse:
    """Get a specific custom agent by name.

    Args:
        name: The agent name.

    Returns:
        Agent details including SOUL.md content.

    Raises:
        HTTPException: 404 if agent not found.
    """
    _require_agents_api_enabled()
    _validate_agent_name(name)
    name = _normalize_agent_name(name)
    user_id = _resolve_agent_user_id(request)

    try:
        return await asyncio.to_thread(_load_agent_response, name, user_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    except Exception as e:
        logger.error(f"Failed to get agent '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get agent: {str(e)}")


@router.post(
    "/agents",
    response_model=AgentResponse,
    status_code=201,
    summary="Create Custom Agent",
    description="Create a new custom agent with its config and SOUL.md.",
)
async def create_agent_endpoint(request: AgentCreateRequest, http_request: Request) -> AgentResponse:
    """Create a new custom agent.

    Args:
        request: The agent creation request.

    Returns:
        The created agent details.

    Raises:
        HTTPException: 409 if agent already exists, 422 if name is invalid.
    """
    _require_agents_api_enabled()
    _validate_agent_name(request.name)
    normalized_name = _normalize_agent_name(request.name)
    if normalized_name == "command-room":
        raise HTTPException(status_code=409, detail="Agent 'command-room' is a built-in system agent")
    user_id = _resolve_agent_user_id(http_request)
    paths = get_paths()

    def _create_agent() -> AgentResponse | None:
        # Worker thread: base-dir resolution, existence checks, directory/file
        # creation, read-back, and failure cleanup are all blocking filesystem
        # IO that must stay off the event loop.
        agent_dir = paths.user_agent_dir(user_id, normalized_name)
        legacy_dir = paths.agent_dir(normalized_name)

        if legacy_dir.exists():
            return None  # signals 409 to the caller

        try:
            try:
                agent_dir.mkdir(parents=True, exist_ok=False)
            except FileExistsError:
                return None  # signals 409 to the caller
            # Write config.yaml
            config_data: dict = {"name": normalized_name}
            if request.description:
                config_data["description"] = request.description
            if request.model is not None:
                config_data["model"] = request.model
            if request.reasoning_effort is not None:
                config_data["reasoning_effort"] = request.reasoning_effort
            if request.tool_groups is not None:
                config_data["tool_groups"] = request.tool_groups
            if request.skills is not None:
                config_data["skills"] = request.skills

            config_file = agent_dir / "config.yaml"
            with open(config_file, "w", encoding="utf-8") as f:
                yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True)

            # Write SOUL.md
            soul_file = agent_dir / "SOUL.md"
            soul_file.write_text(request.soul, encoding="utf-8")

            logger.info(f"Created agent '{normalized_name}' at {agent_dir}")

            agent_cfg = load_agent_config(normalized_name, user_id=user_id)
            return _agent_config_to_response(agent_cfg, include_soul=True, user_id=user_id)
        except Exception:
            # Clean up partial state on failure before surfacing the error.
            if agent_dir.exists():
                shutil.rmtree(agent_dir)
            raise

    try:
        response = await asyncio.to_thread(_create_agent)
    except Exception as e:
        logger.error(f"Failed to create agent '{request.name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create agent: {str(e)}")

    if response is None:
        raise HTTPException(status_code=409, detail=f"Agent '{normalized_name}' already exists")

    return response


@router.put(
    "/agents/{name}",
    response_model=AgentResponse,
    summary="Update Custom Agent",
    description="Update an existing custom agent's config and/or SOUL.md.",
)
async def update_agent(name: str, request: AgentUpdateRequest, http_request: Request) -> AgentResponse:
    """Update an existing custom agent.

    Args:
        name: The agent name.
        request: The update request (all fields optional).

    Returns:
        The updated agent details.

    Raises:
        HTTPException: 404 if agent not found.
    """
    _require_agents_api_enabled()
    _validate_agent_name(name)
    name = _normalize_agent_name(name)
    user_id = _resolve_agent_user_id(http_request)

    try:
        agent_cfg = await asyncio.to_thread(load_agent_config, name, user_id=user_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    paths = get_paths()
    agent_dir = paths.user_agent_dir(user_id, name)
    user_exists, legacy_exists = await asyncio.to_thread(_path_pair_exists, agent_dir, paths.agent_dir(name))
    if not user_exists and legacy_exists:
        raise HTTPException(
            status_code=409,
            detail=(f"Agent '{name}' only exists in the legacy shared layout and is not scoped to a user. Run scripts/migrate_user_isolation.py to move legacy agents into the per-user layout before updating."),
        )
    if name == "command-room":
        await asyncio.to_thread(agent_dir.mkdir, parents=True, exist_ok=True)

    try:
        # Update config if any config fields changed
        # Use model_fields_set to distinguish "field omitted" from "explicitly set to null".
        # This is critical for skills where None means "inherit all" (not "don't change").
        fields_set = request.model_fields_set
        config_changed = bool(fields_set & {"description", "model", "reasoning_effort", "tool_groups", "skills"})

        if config_changed:
            updated: dict = {
                "name": agent_cfg.name,
                "description": request.description if "description" in fields_set else agent_cfg.description,
            }
            new_model = request.model if "model" in fields_set else agent_cfg.model
            if new_model is not None:
                updated["model"] = new_model

            new_reasoning_effort = request.reasoning_effort if "reasoning_effort" in fields_set else agent_cfg.reasoning_effort
            if new_reasoning_effort is not None:
                updated["reasoning_effort"] = new_reasoning_effort

            new_tool_groups = request.tool_groups if "tool_groups" in fields_set else agent_cfg.tool_groups
            if new_tool_groups is not None:
                updated["tool_groups"] = new_tool_groups

            # skills: None = inherit all, [] = no skills, ["a","b"] = whitelist
            if "skills" in fields_set:
                new_skills = request.skills
            else:
                new_skills = agent_cfg.skills
            if new_skills is not None:
                updated["skills"] = new_skills

            config_file = agent_dir / "config.yaml"
            config_text = yaml.dump(updated, default_flow_style=False, allow_unicode=True)
            await asyncio.to_thread(config_file.write_text, config_text, encoding="utf-8")

        # Update SOUL.md if provided
        if request.soul is not None:
            soul_path = agent_dir / "SOUL.md"
            await asyncio.to_thread(soul_path.write_text, request.soul, encoding="utf-8")

        logger.info(f"Updated agent '{name}'")

        refreshed_cfg = await asyncio.to_thread(load_agent_config, name, user_id=user_id)
        return await asyncio.to_thread(
            _agent_config_to_response,
            refreshed_cfg,
            include_soul=True,
            user_id=user_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update agent '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update agent: {str(e)}")


@router.get(
    "/roles",
    response_model=RolesListResponse,
    summary="List Professional Roles",
    description="List reusable Command Room roles and their effective model assignments.",
)
async def list_roles(request: Request, config: AppConfig = Depends(get_config)) -> RolesListResponse:
    _require_agents_api_enabled()
    user_id = _resolve_agent_user_id(request)
    try:
        assignments = await asyncio.to_thread(load_role_assignments, user_id)
        return RolesListResponse(roles=[_role_to_response(name, config, assignments) for name in COMMAND_ROOM_ROLE_CONFIGS])
    except Exception as e:
        logger.error("Failed to list professional roles: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list professional roles: {str(e)}")


@router.put(
    "/roles/{name}",
    response_model=RoleResponse,
    summary="Update Professional Role",
    description="Persist a per-user model assignment for one reusable Command Room role.",
)
async def update_role(
    name: str,
    body: RoleUpdateRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
) -> RoleResponse:
    _require_agents_api_enabled()
    if name not in COMMAND_ROOM_ROLE_CONFIGS:
        raise HTTPException(status_code=404, detail=f"Professional role '{name}' not found")
    _validate_role_model(body, config)
    user_id = _resolve_agent_user_id(request)

    def _save_assignment() -> RoleAssignments:
        return update_role_assignment(
            user_id,
            name,
            RoleAssignment(model=body.model, reasoning_effort=body.reasoning_effort),
        )

    try:
        assignments = await asyncio.to_thread(_save_assignment)
        return _role_to_response(name, config, assignments)
    except Exception as e:
        logger.error("Failed to update professional role '%s': %s", name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update professional role: {str(e)}")


class UserProfileResponse(BaseModel):
    """Response model for the global user profile (USER.md)."""

    content: str | None = Field(default=None, description="USER.md content, or null if not yet created")


class UserProfileUpdateRequest(BaseModel):
    """Request body for setting the global user profile."""

    content: str = Field(default="", description="USER.md content — describes the user's background and preferences")


@router.get(
    "/user-profile",
    response_model=UserProfileResponse,
    summary="Get User Profile",
    description="Read the global USER.md file that is injected into all custom agents.",
)
async def get_user_profile(request: Request) -> UserProfileResponse:
    """Return the current USER.md content.

    Returns:
        UserProfileResponse with content=None if USER.md does not exist yet.
    """
    _require_agents_api_enabled()
    await require_admin_user(request, detail="Admin access is required to read the global user profile")

    try:
        user_md_path = get_paths().user_md_file
        raw = await asyncio.to_thread(_read_optional_text, user_md_path)
        if raw is None:
            return UserProfileResponse(content=None)
        return UserProfileResponse(content=raw or None)
    except Exception as e:
        logger.error(f"Failed to read user profile: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to read user profile: {str(e)}")


@router.put(
    "/user-profile",
    response_model=UserProfileResponse,
    summary="Update User Profile",
    description="Write the global USER.md file that is injected into all custom agents.",
)
async def update_user_profile(body: UserProfileUpdateRequest, request: Request) -> UserProfileResponse:
    """Create or overwrite the global USER.md.

    Args:
        body: The update request with the new USER.md content.

    Returns:
        UserProfileResponse with the saved content.
    """
    _require_agents_api_enabled()
    await require_admin_user(request, detail="Admin access is required to update the global user profile")

    try:
        paths = get_paths()
        await asyncio.to_thread(paths.base_dir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(paths.user_md_file.write_text, body.content, encoding="utf-8")
        logger.info(f"Updated USER.md at {paths.user_md_file}")
        return UserProfileResponse(content=body.content or None)
    except Exception as e:
        logger.error(f"Failed to update user profile: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update user profile: {str(e)}")


@router.delete(
    "/agents/{name}",
    status_code=204,
    summary="Delete Custom Agent",
    description="Delete a custom agent and all its files (config, SOUL.md, memory).",
)
async def delete_agent(name: str, request: Request) -> None:
    """Delete a custom agent.

    Args:
        name: The agent name.

    Raises:
        HTTPException: 404 if no per-user copy exists; 409 if only a legacy
            shared copy exists (suggesting the migration script).
    """
    _require_agents_api_enabled()
    _validate_agent_name(name)
    name = _normalize_agent_name(name)
    if name == "command-room":
        raise HTTPException(status_code=409, detail="Agent 'command-room' is a built-in system agent and cannot be deleted")
    user_id = _resolve_agent_user_id(request)
    paths = get_paths()

    def _remove_agent_dir() -> tuple[str, str]:
        # Runs in a worker thread: resolving the base dir, probing the directory
        # (`exists`), and removing it (`rmtree`) are all blocking filesystem IO
        # that must stay off the event loop.
        agent_dir = paths.user_agent_dir(user_id, name)
        if not agent_dir.exists():
            outcome = "legacy" if paths.agent_dir(name).exists() else "missing"
            return outcome, str(agent_dir)
        shutil.rmtree(agent_dir)
        return "deleted", str(agent_dir)

    try:
        outcome, agent_dir = await asyncio.to_thread(_remove_agent_dir)
    except Exception as e:
        logger.error(f"Failed to delete agent '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete agent: {str(e)}")

    if outcome == "legacy":
        raise HTTPException(
            status_code=409,
            detail=(f"Agent '{name}' only exists in the legacy shared layout and is not scoped to a user. Run scripts/migrate_user_isolation.py to move legacy agents into the per-user layout before deleting."),
        )
    if outcome == "missing":
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    logger.info(f"Deleted agent '{name}' from {agent_dir}")
