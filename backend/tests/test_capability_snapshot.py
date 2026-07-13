from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from _router_auth_helpers import make_authed_test_app
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.deps import get_config
from app.gateway.routers import capabilities
from deerflow.capabilities import build_capability_snapshot
from deerflow.config.app_config import AppConfig
from deerflow.config.extensions_config import ExtensionsConfig, McpOAuthConfig, McpServerConfig, SkillCatalogSourceConfig
from deerflow.config.model_config import ModelConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.config.skills_config import SkillsConfig
from deerflow.config.tool_config import ToolConfig
from deerflow.config.tool_search_config import ToolSearchConfig

_USER_ID = UUID("55555555-5555-5555-5555-555555555555")


def _write_skill(root: Path, category: str, name: str) -> None:
    skill_dir = root / category / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} skill\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def _app_config(tmp_path: Path) -> AppConfig:
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "public", "command-room-chair")
    _write_skill(skills_root, "custom", "local-helper")

    return AppConfig(
        models=[
            ModelConfig(
                name="main",
                use="langchain_openai.ChatOpenAI",
                model="gpt-4.1",
                supports_vision=True,
                supports_reasoning_effort=True,
                reasoning_efforts=["medium", "high", "xhigh"],
                default_reasoning_effort="xhigh",
                api_key="sk-live-secret",
            )
        ],
        sandbox=SandboxConfig(
            use="deerflow.sandbox.local:LocalSandboxProvider",
            environment={"SANDBOX_TOKEN": "sandbox-secret"},
        ),
        tools=[
            ToolConfig(name="read_file", group="file:read", use="deerflow.sandbox.tools:read_file_tool"),
            ToolConfig(name="bash", group="bash", use="deerflow.sandbox.tools:bash_tool"),
        ],
        skills=SkillsConfig(path=str(skills_root)),
        extensions=ExtensionsConfig(
            mcpServers={
                "github": McpServerConfig(
                    enabled=True,
                    command="npx",
                    args=["-y", "@modelcontextprotocol/server-github"],
                    env={"GITHUB_TOKEN": "ghp_secret"},
                    headers={"Authorization": "Bearer real-token"},
                    oauth=McpOAuthConfig(
                        token_url="https://auth.example/token",
                        client_id="client-id",
                        client_secret="client-secret",
                        refresh_token="refresh-secret",
                    ),
                    description="GitHub MCP",
                )
            },
            skillCatalogSources={
                "official": SkillCatalogSourceConfig(
                    enabled=True,
                    url="https://example.com/catalog.json",
                    trustLevel="official",
                    description="Official catalog",
                )
            },
        ),
        tool_search=ToolSearchConfig(enabled=True),
        subagents={
            "model": "main",
            "reasoning_effort": "xhigh",
            "timeout_seconds": 3600,
        },
    )


def _user() -> User:
    return User(id=_USER_ID, email="capabilities@example.com", password_hash="x", system_role="user")


def _router_app(config: AppConfig) -> FastAPI:
    app = make_authed_test_app(user_factory=_user)
    app.include_router(capabilities.router)
    app.dependency_overrides[get_config] = lambda: config
    return app


def test_capability_snapshot_contains_required_facts_and_masks_secrets(tmp_path: Path):
    snapshot = build_capability_snapshot(_app_config(tmp_path), thread_id="thread-1", user_id="user-1")

    assert snapshot["version"] == 2
    assert snapshot["user_id"] == "user-1"
    assert snapshot["thread_id"] == "thread-1"
    for field in (
        "models",
        "subagents",
        "tools",
        "skills",
        "skill_catalog_sources",
        "mcp_servers",
        "sandbox",
        "approval_policy",
        "middleware_stack",
        "filesystem_permissions",
        "agent_harness_profiles",
        "capability_release",
        "capability_center",
    ):
        assert field in snapshot

    dumped = json.dumps(snapshot)
    for secret in ("sk-live-secret", "ghp_secret", "Bearer real-token", "client-secret", "refresh-secret", "sandbox-secret"):
        assert secret not in dumped

    assert snapshot["models"][0]["extra"]["api_key"] == "***"
    assert snapshot["models"][0]["reasoning_efforts"] == ["medium", "high", "xhigh"]
    assert snapshot["models"][0]["default_reasoning_effort"] == "xhigh"
    assert snapshot["mcp_servers"][0]["env"] == {"GITHUB_TOKEN": "***"}
    assert snapshot["mcp_servers"][0]["headers"] == {"Authorization": "***"}
    assert snapshot["mcp_servers"][0]["oauth"]["client_secret"] == "***"
    assert snapshot["mcp_servers"][0]["oauth"]["refresh_token"] == "***"
    assert snapshot["sandbox"]["environment"] == {"SANDBOX_TOKEN": "***"}
    assert snapshot["skill_catalog_sources"][0]["name"] == "official"
    assert snapshot["skill_catalog_sources"][0]["trustLevel"] == "official"

    center_dumped = json.dumps(snapshot["capability_center"])
    for secret in ("sk-live-secret", "ghp_secret", "Bearer real-token", "client-secret", "refresh-secret", "sandbox-secret"):
        assert secret not in center_dumped


def test_capability_center_exposes_advisory_facts_without_decisions(tmp_path: Path):
    snapshot = build_capability_snapshot(_app_config(tmp_path), thread_id="thread-1")
    center = snapshot["capability_center"]

    assert center["advisory_only"] is True
    assert center["stop_before"] == snapshot["approval_policy"]["stop_before"]
    assert "credential or raw sensitive-data disclosure" in center["stop_before"]
    assert set(center["evidence_refs"]) >= {
        "deerflow.capabilities.snapshot",
        "config.tools",
        "config.sandbox",
        "skill_storage",
        "lead_agent.build_middlewares",
    }
    assert center["current_release"]["thread_scoped"] is True
    assert center["current_release"]["program_decides_next_step"] is False
    assert center["permission_facts"]["approval_requirements"] == {
        "mcp_config_admin_required": True,
        "global_skill_management_admin_required": True,
        "thread_owner_check_required": True,
    }
    assert center["permission_facts"]["program_makes_next_step_decisions"] is False
    assert center["non_decisions"] == {
        "program_makes_next_step_decisions": False,
        "auto_authorize": False,
        "auto_reject": False,
        "auto_pass_fail": False,
        "auto_dispatch": False,
        "auto_rework": False,
    }


def test_capability_snapshot_labels_tools_skills_middleware_and_policy(tmp_path: Path):
    snapshot = build_capability_snapshot(_app_config(tmp_path))

    tools = {item["name"]: item for item in snapshot["tools"]}
    assert tools["read_file"]["read_only"] is True
    assert tools["bash"]["risk_level"] == "high"
    assert tools["bash"]["requires_approval"] is True
    assert "task" in tools
    assert "tool_search" in tools

    skills = {item["name"]: item for item in snapshot["skills"]}
    assert skills["command-room-chair"]["enabled"] is True
    assert skills["local-helper"]["category"] == "custom"

    middleware = {item["name"]: item for item in snapshot["middleware_stack"]}
    assert "CommandRoomRoundContextMiddleware" in middleware
    assert snapshot["approval_policy"]["program_makes_next_step_decisions"] is False
    assert "credential or raw sensitive-data disclosure" in snapshot["approval_policy"]["stop_before"]
    assert {item["label"] for item in snapshot["filesystem_permissions"]} >= {"read", "write", "execute", "approval_required", "denied"}


def test_command_room_runtime_snapshot_reports_direct_and_codex_transport_facts(tmp_path: Path, monkeypatch):
    from deerflow.config import paths as paths_module

    home = tmp_path / "home"
    agent_dir = home / "users" / "user-1" / "agents" / "command-room"
    agent_dir.mkdir(parents=True)
    (agent_dir / "config.yaml").write_text(
        "\n".join(
            [
                "name: command-room",
                "model: missing-model",
                "skills:",
                "  - command-room-chair",
                "  - missing-skill",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DEER_FLOW_HOME", str(home))
    monkeypatch.setattr(paths_module, "_paths", None)
    snapshot = build_capability_snapshot(_app_config(tmp_path), user_id="user-1")
    runtime = snapshot["command_room_runtime"]

    assert runtime["agent_config"]["status"] == "loaded"
    assert runtime["agent_config"]["requested_model"] == "missing-model"
    assert runtime["agent_config"]["resolved_model"] == "main"
    assert runtime["agent_config"]["model_fallback"] is True
    assert runtime["skills"]["loaded"] == ["command-room-chair"]
    assert runtime["skills"]["missing"] == ["missing-skill"]
    assert runtime["direct"]["include_mcp"] is False
    assert runtime["direct"]["tool_groups"] == []
    assert runtime["direct"]["configured_tools"] == ["ask_clarification", "present_files", "task"]
    assert "bash" not in runtime["direct"]["configured_tools"]
    assert runtime["task_transport"] == {
        "runtime": "codex-cli-one-shot",
        "model": "gpt-4.1",
        "configured_model": "main",
        "reasoning_effort": "xhigh",
        "timeout_seconds": 3600,
        "sandbox_mode": "workspace-write",
        "workspace_source": "thread_data.workspace_path",
        "inherits_deerflow_tools": False,
        "inherits_deerflow_skills": False,
        "inherits_deerflow_mcp": False,
        "programmatic_turn_loop": False,
        "process_ends_after_result": True,
    }
    assert [profile["agent_name"] for profile in snapshot["agent_harness_profiles"]] == ["command-room"]


def test_mcp_cache_status_is_read_only_and_reports_loaded_tools(monkeypatch):
    from deerflow.mcp import cache

    monkeypatch.setattr(cache, "_cache_initialized", True)
    monkeypatch.setattr(cache, "_mcp_tools_cache", [SimpleNamespace(name="search"), SimpleNamespace(name="read")])
    monkeypatch.setattr(cache, "_config_mtime", None)

    assert cache.get_mcp_cache_status() == {
        "initialized": True,
        "stale": False,
        "tool_count": 2,
        "tool_names": ["search", "read"],
        "last_error_type": None,
    }


def test_capability_api_returns_global_snapshot(tmp_path: Path):
    app = _router_app(_app_config(tmp_path))

    with TestClient(app) as client:
        response = client.get("/api/capabilities")

    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == str(_USER_ID)
    assert data["thread_id"] is None
    assert data["models"][0]["name"] == "main"


def test_thread_capability_api_returns_thread_scoped_snapshot(tmp_path: Path):
    app = _router_app(_app_config(tmp_path))

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/capabilities")

    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == str(_USER_ID)
    assert data["thread_id"] == "thread-1"


def test_capability_routes_require_auth_when_mounted_without_middleware(tmp_path: Path):
    app = FastAPI()
    app.include_router(capabilities.router)
    app.dependency_overrides[get_config] = lambda: _app_config(tmp_path)

    with TestClient(app) as client:
        assert client.get("/api/capabilities").status_code == 401
        assert client.get("/api/threads/thread-1/capabilities").status_code == 401
