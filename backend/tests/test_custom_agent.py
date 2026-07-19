"""Tests for custom agent support."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml
from fastapi.testclient import TestClient

from deerflow.config.agents_api_config import AgentsApiConfig, get_agents_api_config, set_agents_api_config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_paths(base_dir: Path):
    """Return a Paths instance pointing to base_dir."""
    from deerflow.config.paths import Paths

    return Paths(base_dir=base_dir)


def _write_agent(base_dir: Path, name: str, config: dict, soul: str = "You are helpful.") -> None:
    """Write an agent directory with config.yaml and SOUL.md."""
    agent_dir = base_dir / "agents" / name
    agent_dir.mkdir(parents=True, exist_ok=True)

    config_copy = dict(config)
    if "name" not in config_copy:
        config_copy["name"] = name

    with open(agent_dir / "config.yaml", "w") as f:
        yaml.dump(config_copy, f)

    (agent_dir / "SOUL.md").write_text(soul, encoding="utf-8")


# ===========================================================================
# 1. Paths class – agent path methods
# ===========================================================================


class TestPaths:
    def test_agents_dir(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.agents_dir == tmp_path / "agents"

    def test_agent_dir(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.agent_dir("code-reviewer") == tmp_path / "agents" / "code-reviewer"

    def test_agent_memory_file(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.agent_memory_file("code-reviewer") == tmp_path / "agents" / "code-reviewer" / "memory.json"

    def test_user_md_file(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.user_md_file == tmp_path / "USER.md"

    def test_paths_are_different_from_global(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.memory_file != paths.agent_memory_file("my-agent")
        assert paths.memory_file == tmp_path / "memory.json"
        assert paths.agent_memory_file("my-agent") == tmp_path / "agents" / "my-agent" / "memory.json"


# ===========================================================================
# 2. AgentConfig – Pydantic parsing
# ===========================================================================


class TestAgentConfig:
    def test_minimal_config(self):
        from deerflow.config.agents_config import AgentConfig

        cfg = AgentConfig(name="my-agent")
        assert cfg.name == "my-agent"
        assert cfg.description == ""
        assert cfg.model is None
        assert cfg.reasoning_effort is None
        assert cfg.tool_groups is None

    def test_full_config(self):
        from deerflow.config.agents_config import AgentConfig

        cfg = AgentConfig(
            name="code-reviewer",
            description="Specialized for code review",
            model="deepseek-v3",
            reasoning_effort="high",
            tool_groups=["file:read", "bash"],
        )
        assert cfg.name == "code-reviewer"
        assert cfg.model == "deepseek-v3"
        assert cfg.reasoning_effort == "high"
        assert cfg.tool_groups == ["file:read", "bash"]

    def test_config_from_dict(self):
        from deerflow.config.agents_config import AgentConfig

        data = {"name": "test-agent", "description": "A test", "model": "gpt-4"}
        cfg = AgentConfig(**data)
        assert cfg.name == "test-agent"
        assert cfg.model == "gpt-4"
        assert cfg.tool_groups is None


# ===========================================================================
# 3. load_agent_config
# ===========================================================================


class TestLoadAgentConfig:
    def test_load_valid_config(self, tmp_path):
        config_dict = {"name": "code-reviewer", "description": "Code review agent", "model": "deepseek-v3"}
        _write_agent(tmp_path, "code-reviewer", config_dict)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("code-reviewer")

        assert cfg.name == "code-reviewer"
        assert cfg.description == "Code review agent"
        assert cfg.model == "deepseek-v3"

    def test_load_missing_agent_raises(self, tmp_path):
        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            with pytest.raises(FileNotFoundError):
                load_agent_config("nonexistent-agent")

    def test_loads_builtin_command_room_without_custom_config(self, tmp_path):
        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            config = load_agent_config("command-room")

        assert config is not None
        assert config.name == "command-room"
        assert config.description.startswith("NextOS")
        assert config.model == "gpt-5.6"
        assert config.reasoning_effort == "max"
        assert config.skills == ["nextos-commander"]

    def test_loads_builtin_command_room_with_memory_only_user_directory(self, tmp_path):
        user_dir = tmp_path / "users" / "u1" / "agents" / "command-room"
        user_dir.mkdir(parents=True)
        (user_dir / "memory.json").write_text("{}", encoding="utf-8")

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            config = load_agent_config("command-room", user_id="u1")

        assert config is not None
        assert config.name == "command-room"
        assert config.description.startswith("NextOS")
        assert config.model == "gpt-5.6"
        assert config.reasoning_effort == "max"
        assert config.skills == ["nextos-commander"]

    def test_load_missing_config_yaml_raises(self, tmp_path):
        # Create directory without config.yaml
        (tmp_path / "agents" / "broken-agent").mkdir(parents=True)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            with pytest.raises(FileNotFoundError):
                load_agent_config("broken-agent")

    def test_load_config_infers_name_from_dir(self, tmp_path):
        """Config without 'name' field should use directory name."""
        agent_dir = tmp_path / "agents" / "inferred-name"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text("description: My agent\n")
        (agent_dir / "SOUL.md").write_text("Hello")

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("inferred-name")

        assert cfg.name == "inferred-name"

    def test_load_config_with_tool_groups(self, tmp_path):
        config_dict = {"name": "restricted", "tool_groups": ["file:read", "file:write"]}
        _write_agent(tmp_path, "restricted", config_dict)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("restricted")

        assert cfg.tool_groups == ["file:read", "file:write"]

    def test_load_config_with_skills_empty_list(self, tmp_path):
        config_dict = {"name": "no-skills-agent", "skills": []}
        _write_agent(tmp_path, "no-skills-agent", config_dict)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("no-skills-agent")

        assert cfg.skills == []

    def test_load_config_with_skills_omitted(self, tmp_path):
        config_dict = {"name": "default-skills-agent"}
        _write_agent(tmp_path, "default-skills-agent", config_dict)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("default-skills-agent")

        assert cfg.skills is None

    def test_legacy_prompt_file_field_ignored(self, tmp_path):
        """Unknown fields like the old prompt_file should be silently ignored."""
        agent_dir = tmp_path / "agents" / "legacy-agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text("name: legacy-agent\nprompt_file: system.md\n")
        (agent_dir / "SOUL.md").write_text("Soul content")

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("legacy-agent")

        assert cfg.name == "legacy-agent"


# ===========================================================================
# 3b. resolve_agent_dir — memory-only directory fallback (#3390)
# ===========================================================================


class TestResolveAgentDirMemoryOnlyFallback:
    """Regression tests for #3390.

    When memory is enabled, the first conversation creates a user-isolated
    agent directory containing only ``memory.json`` (no ``config.yaml``).
    On the next turn ``resolve_agent_dir`` must fall through to the legacy
    shared layout instead of returning the incomplete user directory.
    """

    def test_user_dir_with_only_memory_falls_back_to_legacy(self, tmp_path):
        """User dir has memory.json but no config.yaml → use legacy dir."""
        from deerflow.config.agents_config import resolve_agent_dir

        # Legacy agent with full config
        legacy_dir = tmp_path / "agents" / "my-agent"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "config.yaml").write_text("name: my-agent\n", encoding="utf-8")
        (legacy_dir / "SOUL.md").write_text("legacy soul", encoding="utf-8")

        # User dir created by memory write — no config.yaml
        user_dir = tmp_path / "users" / "u1" / "agents" / "my-agent"
        user_dir.mkdir(parents=True)
        (user_dir / "memory.json").write_text("{}", encoding="utf-8")

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)), patch("deerflow.config.agents_config.get_effective_user_id", return_value="u1"):
            result = resolve_agent_dir("my-agent", user_id="u1")

        assert result == legacy_dir

    def test_user_dir_with_config_takes_priority(self, tmp_path):
        """User dir with config.yaml should still win over legacy."""
        from deerflow.config.agents_config import resolve_agent_dir

        # Legacy
        legacy_dir = tmp_path / "agents" / "my-agent"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "config.yaml").write_text("name: my-agent\n", encoding="utf-8")

        # User dir with full config (migrated)
        user_dir = tmp_path / "users" / "u1" / "agents" / "my-agent"
        user_dir.mkdir(parents=True)
        (user_dir / "config.yaml").write_text("name: my-agent\nmodel: gpt-4\n", encoding="utf-8")
        (user_dir / "memory.json").write_text("{}", encoding="utf-8")

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)), patch("deerflow.config.agents_config.get_effective_user_id", return_value="u1"):
            result = resolve_agent_dir("my-agent", user_id="u1")

        assert result == user_dir

    def test_load_config_falls_back_when_user_dir_is_memory_only(self, tmp_path):
        """End-to-end: load_agent_config works when user dir only has memory.json."""
        config_dict = {"name": "my-agent", "description": "Legacy agent", "model": "deepseek-v3"}
        _write_agent(tmp_path, "my-agent", config_dict)

        # Simulate memory write creating user dir without config
        user_dir = tmp_path / "users" / "u1" / "agents" / "my-agent"
        user_dir.mkdir(parents=True)
        (user_dir / "memory.json").write_text("{}", encoding="utf-8")

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)), patch("deerflow.config.agents_config.get_effective_user_id", return_value="u1"):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("my-agent", user_id="u1")

        assert cfg.name == "my-agent"
        assert cfg.model == "deepseek-v3"


# ===========================================================================
# 4. load_agent_soul
# ===========================================================================


class TestLoadAgentSoul:
    def test_reads_soul_file(self, tmp_path):
        expected_soul = "You are a specialized code review expert."
        _write_agent(tmp_path, "code-reviewer", {"name": "code-reviewer"}, soul=expected_soul)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import AgentConfig, load_agent_soul

            cfg = AgentConfig(name="code-reviewer")
            soul = load_agent_soul(cfg.name)

        assert soul == expected_soul

    def test_missing_soul_file_returns_none(self, tmp_path):
        agent_dir = tmp_path / "agents" / "no-soul"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text("name: no-soul\n")
        # No SOUL.md created

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import AgentConfig, load_agent_soul

            cfg = AgentConfig(name="no-soul")
            soul = load_agent_soul(cfg.name)

        assert soul is None

    def test_empty_soul_file_returns_none(self, tmp_path):
        agent_dir = tmp_path / "agents" / "empty-soul"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text("name: empty-soul\n")
        (agent_dir / "SOUL.md").write_text("   \n   ")

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import AgentConfig, load_agent_soul

            cfg = AgentConfig(name="empty-soul")
            soul = load_agent_soul(cfg.name)

        assert soul is None


# ===========================================================================
# 5. list_custom_agents
# ===========================================================================


class TestListCustomAgents:
    def test_empty_when_no_agents_dir(self, tmp_path):
        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        assert agents == []

    def test_discovers_multiple_agents(self, tmp_path):
        _write_agent(tmp_path, "agent-a", {"name": "agent-a"})
        _write_agent(tmp_path, "agent-b", {"name": "agent-b", "description": "B"})

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        names = [a.name for a in agents]
        assert "agent-a" in names
        assert "agent-b" in names

    def test_skips_dirs_without_config_yaml(self, tmp_path):
        # Valid agent
        _write_agent(tmp_path, "valid-agent", {"name": "valid-agent"})
        # Invalid dir (no config.yaml)
        (tmp_path / "agents" / "invalid-dir").mkdir(parents=True)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        assert len(agents) == 1
        assert agents[0].name == "valid-agent"

    def test_skips_non_directory_entries(self, tmp_path):
        # Create the agents dir with a file (not a dir)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "not-a-dir.txt").write_text("hello")
        _write_agent(tmp_path, "real-agent", {"name": "real-agent"})

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        assert len(agents) == 1
        assert agents[0].name == "real-agent"

    def test_returns_sorted_by_name(self, tmp_path):
        _write_agent(tmp_path, "z-agent", {"name": "z-agent"})
        _write_agent(tmp_path, "a-agent", {"name": "a-agent"})
        _write_agent(tmp_path, "m-agent", {"name": "m-agent"})

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        names = [a.name for a in agents]
        assert names == sorted(names)


# ===========================================================================
# 7. Memory isolation: _get_memory_file_path
# ===========================================================================


class TestMemoryFilePath:
    def test_global_memory_path(self, tmp_path):
        """None agent_name should return global memory file."""
        from deerflow.agents.memory.storage import FileMemoryStorage
        from deerflow.config.memory_config import MemoryConfig

        with (
            patch("deerflow.agents.memory.storage.get_paths", return_value=_make_paths(tmp_path)),
            patch("deerflow.agents.memory.storage.get_memory_config", return_value=MemoryConfig(storage_path="")),
        ):
            storage = FileMemoryStorage()
            path = storage._get_memory_file_path(None)
        assert path == tmp_path / "memory.json"

    def test_agent_memory_path(self, tmp_path):
        """Providing agent_name should return per-agent memory file."""
        from deerflow.agents.memory.storage import FileMemoryStorage
        from deerflow.config.memory_config import MemoryConfig

        with (
            patch("deerflow.agents.memory.storage.get_paths", return_value=_make_paths(tmp_path)),
            patch("deerflow.agents.memory.storage.get_memory_config", return_value=MemoryConfig(storage_path="")),
        ):
            storage = FileMemoryStorage()
            path = storage._get_memory_file_path("code-reviewer")
        assert path == tmp_path / "agents" / "code-reviewer" / "memory.json"

    def test_different_paths_for_different_agents(self, tmp_path):
        from deerflow.agents.memory.storage import FileMemoryStorage
        from deerflow.config.memory_config import MemoryConfig

        with (
            patch("deerflow.agents.memory.storage.get_paths", return_value=_make_paths(tmp_path)),
            patch("deerflow.agents.memory.storage.get_memory_config", return_value=MemoryConfig(storage_path="")),
        ):
            storage = FileMemoryStorage()
            path_global = storage._get_memory_file_path(None)
            path_a = storage._get_memory_file_path("agent-a")
            path_b = storage._get_memory_file_path("agent-b")

        assert path_global != path_a
        assert path_global != path_b
        assert path_a != path_b


# ===========================================================================
# 8. Gateway API – Agents endpoints
# ===========================================================================


def _make_test_app(tmp_path: Path, *, system_role: str = "admin"):
    """Create a FastAPI app with the agents router, patching paths to tmp_path."""
    from fastapi import FastAPI

    from app.gateway.deps import get_config
    from app.gateway.routers.agents import router
    from deerflow.config.subagents_config import SubagentOverrideConfig, SubagentsAppConfig

    app = FastAPI()
    models = {
        "gpt-5.6": SimpleNamespace(
            name="gpt-5.6",
            model="gpt-5.6-sol",
            supports_reasoning_effort=True,
            reasoning_efforts=["high", "xhigh", "max"],
            default_reasoning_effort="max",
        ),
        "gpt-5.6-terra": SimpleNamespace(
            name="gpt-5.6-terra",
            model="gpt-5.6-terra",
            supports_reasoning_effort=True,
            reasoning_efforts=["high", "xhigh"],
            default_reasoning_effort="xhigh",
        ),
    }
    app_config = SimpleNamespace(
        models=list(models.values()),
        subagents=SubagentsAppConfig(
            model="gpt-5.6-terra",
            reasoning_effort="xhigh",
            agents={"planner": SubagentOverrideConfig(model="gpt-5.6", reasoning_effort="max")},
        ),
        get_model_config=models.get,
    )
    app.dependency_overrides[get_config] = lambda: app_config

    @app.middleware("http")
    async def _set_test_user(request, call_next):
        request.state.user = SimpleNamespace(id="test-user-autouse", system_role=system_role)
        return await call_next(request)

    app.include_router(router)
    return app


@pytest.fixture()
def agent_client(tmp_path):
    """TestClient with agents router, using tmp_path as base_dir."""
    import app.gateway.routers.agents as agents_router

    paths_instance = _make_paths(tmp_path)
    previous_config = AgentsApiConfig(**get_agents_api_config().model_dump())

    with (
        patch("deerflow.config.agents_config.get_paths", return_value=paths_instance),
        patch("deerflow.config.role_assignments.get_paths", return_value=paths_instance),
        patch.object(agents_router, "get_paths", return_value=paths_instance),
    ):
        set_agents_api_config(AgentsApiConfig(enabled=True))
        try:
            app = _make_test_app(tmp_path)
            with TestClient(app) as client:
                client._tmp_path = tmp_path  # type: ignore[attr-defined]
                yield client
        finally:
            set_agents_api_config(previous_config)


@pytest.fixture()
def disabled_agent_client(tmp_path):
    """TestClient with agents router while the management API is disabled."""
    import app.gateway.routers.agents as agents_router

    paths_instance = _make_paths(tmp_path)
    previous_config = AgentsApiConfig(**get_agents_api_config().model_dump())

    with (
        patch("deerflow.config.agents_config.get_paths", return_value=paths_instance),
        patch("deerflow.config.role_assignments.get_paths", return_value=paths_instance),
        patch.object(agents_router, "get_paths", return_value=paths_instance),
    ):
        set_agents_api_config(AgentsApiConfig(enabled=False))
        try:
            app = _make_test_app(tmp_path)
            with TestClient(app) as client:
                yield client
        finally:
            set_agents_api_config(previous_config)


class TestAgentsAPI:
    def test_list_agents_empty(self, agent_client):
        response = agent_client.get("/api/agents")
        assert response.status_code == 200
        data = response.json()
        assert [agent["name"] for agent in data["agents"]] == ["command-room"]
        assert data["agents"][0]["system"] is True

    def test_create_agent(self, agent_client):
        payload = {
            "name": "code-reviewer",
            "description": "Reviews code",
            "soul": "You are a code reviewer.",
        }
        response = agent_client.post("/api/agents", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "code-reviewer"
        assert data["description"] == "Reviews code"
        assert data["soul"] == "You are a code reviewer."

    def test_create_agent_invalid_name(self, agent_client):
        payload = {"name": "Code Reviewer!", "soul": "test"}
        response = agent_client.post("/api/agents", json=payload)
        assert response.status_code == 422

    def test_create_duplicate_agent_409(self, agent_client):
        payload = {"name": "my-agent", "soul": "test"}
        agent_client.post("/api/agents", json=payload)

        # Second create should fail
        response = agent_client.post("/api/agents", json=payload)
        assert response.status_code == 409

    def test_list_agents_after_create(self, agent_client):
        agent_client.post("/api/agents", json={"name": "agent-one", "soul": "p1"})
        agent_client.post("/api/agents", json={"name": "agent-two", "soul": "p2"})

        response = agent_client.get("/api/agents")
        assert response.status_code == 200
        names = [a["name"] for a in response.json()["agents"]]
        assert "agent-one" in names
        assert "agent-two" in names

    def test_list_agents_includes_soul(self, agent_client):
        agent_client.post("/api/agents", json={"name": "soul-agent", "soul": "My soul content"})

        response = agent_client.get("/api/agents")
        assert response.status_code == 200
        agents = response.json()["agents"]
        soul_agent = next(a for a in agents if a["name"] == "soul-agent")
        assert soul_agent["soul"] == "My soul content"

    def test_get_agent(self, agent_client):
        agent_client.post("/api/agents", json={"name": "test-agent", "soul": "Hello world"})

        response = agent_client.get("/api/agents/test-agent")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "test-agent"
        assert data["soul"] == "Hello world"

    def test_gets_builtin_command_room_without_custom_config(self, agent_client):
        response = agent_client.get("/api/agents/command-room")

        assert response.status_code == 200
        assert response.json()["name"] == "command-room"
        assert response.json()["system"] is True

    def test_updates_builtin_command_room_model_without_restart(self, agent_client, tmp_path):
        response = agent_client.put(
            "/api/agents/command-room",
            json={"model": "gpt-5.6-terra", "reasoning_effort": "xhigh"},
        )

        assert response.status_code == 200
        assert response.json()["model"] == "gpt-5.6-terra"
        assert response.json()["reasoning_effort"] == "xhigh"
        config_path = tmp_path / "users" / "test-user-autouse" / "agents" / "command-room" / "config.yaml"
        assert config_path.is_file()

    def test_command_room_cannot_be_created_or_deleted(self, agent_client):
        create_response = agent_client.post("/api/agents", json={"name": "command-room", "soul": "x"})
        delete_response = agent_client.delete("/api/agents/command-room")

        assert create_response.status_code == 409
        assert delete_response.status_code == 409

    def test_get_missing_agent_404(self, agent_client):
        response = agent_client.get("/api/agents/nonexistent")
        assert response.status_code == 404

    def test_update_agent_soul(self, agent_client):
        agent_client.post("/api/agents", json={"name": "update-me", "soul": "original"})

        response = agent_client.put("/api/agents/update-me", json={"soul": "updated"})
        assert response.status_code == 200
        assert response.json()["soul"] == "updated"

    def test_update_agent_description(self, agent_client):
        agent_client.post("/api/agents", json={"name": "desc-agent", "description": "old desc", "soul": "p"})

        response = agent_client.put("/api/agents/desc-agent", json={"description": "new desc"})
        assert response.status_code == 200
        assert response.json()["description"] == "new desc"

    def test_update_missing_agent_404(self, agent_client):
        response = agent_client.put("/api/agents/ghost-agent", json={"soul": "new"})
        assert response.status_code == 404

    def test_delete_agent(self, agent_client):
        agent_client.post("/api/agents", json={"name": "del-me", "soul": "bye"})

        response = agent_client.delete("/api/agents/del-me")
        assert response.status_code == 204

        # Verify it's gone
        response = agent_client.get("/api/agents/del-me")
        assert response.status_code == 404

    def test_delete_missing_agent_404(self, agent_client):
        response = agent_client.delete("/api/agents/does-not-exist")
        assert response.status_code == 404

    def test_create_agent_with_model_and_tool_groups(self, agent_client):
        payload = {
            "name": "specialized",
            "description": "Specialized agent",
            "model": "deepseek-v3",
            "reasoning_effort": "high",
            "tool_groups": ["file:read", "bash"],
            "soul": "You are specialized.",
        }
        response = agent_client.post("/api/agents", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["model"] == "deepseek-v3"
        assert data["reasoning_effort"] == "high"
        assert data["tool_groups"] == ["file:read", "bash"]

    def test_create_persists_files_on_disk(self, agent_client, tmp_path):
        agent_client.post("/api/agents", json={"name": "disk-check", "soul": "disk soul"})

        # tests/conftest.py installs an autouse fixture that sets the
        # contextvar to "test-user-autouse", so the agent is persisted under
        # users/test-user-autouse/agents/ rather than the legacy shared dir.
        agent_dir = tmp_path / "users" / "test-user-autouse" / "agents" / "disk-check"
        assert agent_dir.exists()
        assert (agent_dir / "config.yaml").exists()
        assert (agent_dir / "SOUL.md").exists()
        assert (agent_dir / "SOUL.md").read_text() == "disk soul"

    def test_delete_removes_files_from_disk(self, agent_client, tmp_path):
        agent_client.post("/api/agents", json={"name": "remove-me", "soul": "bye"})
        agent_dir = tmp_path / "users" / "test-user-autouse" / "agents" / "remove-me"
        assert agent_dir.exists()

        agent_client.delete("/api/agents/remove-me")
        assert not agent_dir.exists()

    def test_create_rejects_legacy_name_collision(self, agent_client, tmp_path):
        """An unmigrated legacy agent must still block name collision so that
        running the migration script later won't shadow the legacy entry."""
        legacy_dir = tmp_path / "agents" / "legacy-agent"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "config.yaml").write_text("name: legacy-agent\n", encoding="utf-8")
        (legacy_dir / "SOUL.md").write_text("legacy soul", encoding="utf-8")

        response = agent_client.post("/api/agents", json={"name": "legacy-agent", "soul": "x"})
        assert response.status_code == 409

    def test_create_agent_uses_internal_owner_header(self, tmp_path):
        import app.gateway.routers.agents as agents_router
        from app.gateway.internal_auth import INTERNAL_OWNER_USER_ID_HEADER_NAME, INTERNAL_SYSTEM_ROLE

        paths_instance = _make_paths(tmp_path)
        previous_config = AgentsApiConfig(**get_agents_api_config().model_dump())
        request = SimpleNamespace(
            headers={INTERNAL_OWNER_USER_ID_HEADER_NAME: "owner-agent"},
            state=SimpleNamespace(user=SimpleNamespace(id="default", system_role=INTERNAL_SYSTEM_ROLE)),
        )

        with patch("deerflow.config.agents_config.get_paths", return_value=paths_instance), patch.object(agents_router, "get_paths", return_value=paths_instance):
            set_agents_api_config(AgentsApiConfig(enabled=True))
            try:
                response = asyncio.run(
                    agents_router.create_agent_endpoint(
                        agents_router.AgentCreateRequest(name="internal-agent", soul="owner soul"),
                        request,
                    )
                )
            finally:
                set_agents_api_config(previous_config)

        assert response.name == "internal-agent"
        owner_dir = tmp_path / "users" / "owner-agent" / "agents" / "internal-agent"
        default_dir = tmp_path / "users" / "default" / "agents" / "internal-agent"
        assert (owner_dir / "SOUL.md").read_text(encoding="utf-8") == "owner soul"
        assert not default_dir.exists()


class TestRolesAPI:
    def test_list_roles_uses_static_defaults(self, agent_client):
        response = agent_client.get("/api/roles")

        assert response.status_code == 200
        roles = {role["name"]: role for role in response.json()["roles"]}
        assert set(roles) == {
            "planner",
            "project-manager",
            "executor",
            "fact-finder",
            "opposition",
            "recorder",
            "runtime-reliability-auditor",
            "persistence-migration-auditor",
            "frontend-protocol-auditor",
            "security-auditor",
            "platform-ops-auditor",
        }
        assert roles["planner"]["model"] == "gpt-5.6"
        assert roles["planner"]["reasoning_effort"] == "max"
        assert roles["executor"]["model"] == "gpt-5.6-terra"
        assert roles["executor"]["skill"] == "command-room-executor"

    def test_update_role_persists_per_user_override(self, agent_client, tmp_path):
        response = agent_client.put(
            "/api/roles/executor",
            json={"model": "gpt-5.6", "reasoning_effort": "max"},
        )

        assert response.status_code == 200
        assert response.json()["model"] == "gpt-5.6"
        assert response.json()["reasoning_effort"] == "max"
        assignments_path = tmp_path / "users" / "test-user-autouse" / "role-assignments.json"
        assert assignments_path.is_file()

        refreshed = agent_client.get("/api/roles").json()["roles"]
        executor = next(role for role in refreshed if role["name"] == "executor")
        assert executor["model"] == "gpt-5.6"
        assert executor["reasoning_effort"] == "max"

    def test_update_role_rejects_unknown_role_model_and_effort(self, agent_client):
        unknown_role = agent_client.put("/api/roles/critic", json={"model": "gpt-5.6", "reasoning_effort": "max"})
        unknown_model = agent_client.put("/api/roles/planner", json={"model": "missing", "reasoning_effort": "max"})
        unsupported_effort = agent_client.put("/api/roles/planner", json={"model": "gpt-5.6-terra", "reasoning_effort": "max"})

        assert unknown_role.status_code == 404
        assert unknown_model.status_code == 422
        assert unsupported_effort.status_code == 422


# ===========================================================================
# 9. Gateway API – User Profile endpoints
# ===========================================================================


class TestUserProfileAPI:
    def test_user_profile_routes_require_admin(self, tmp_path):
        import app.gateway.routers.agents as agents_router

        paths_instance = _make_paths(tmp_path)
        previous_config = AgentsApiConfig(**get_agents_api_config().model_dump())
        with patch.object(agents_router, "get_paths", return_value=paths_instance):
            set_agents_api_config(AgentsApiConfig(enabled=True))
            try:
                with TestClient(_make_test_app(tmp_path, system_role="user")) as client:
                    assert client.get("/api/user-profile").status_code == 403
                    assert client.put("/api/user-profile", json={"content": "blocked"}).status_code == 403
            finally:
                set_agents_api_config(previous_config)

    def test_get_user_profile_empty(self, agent_client):
        response = agent_client.get("/api/user-profile")
        assert response.status_code == 200
        assert response.json()["content"] is None

    def test_put_user_profile(self, agent_client, tmp_path):
        content = "# User Profile\n\nI am a developer."
        response = agent_client.put("/api/user-profile", json={"content": content})
        assert response.status_code == 200
        assert response.json()["content"] == content

        # File should be written to disk
        user_md = tmp_path / "USER.md"
        assert user_md.exists()
        assert user_md.read_text(encoding="utf-8") == content

    def test_get_user_profile_after_put(self, agent_client):
        content = "# Profile\n\nI work on data science."
        agent_client.put("/api/user-profile", json={"content": content})

        response = agent_client.get("/api/user-profile")
        assert response.status_code == 200
        assert response.json()["content"] == content

    def test_put_empty_user_profile_returns_none(self, agent_client):
        response = agent_client.put("/api/user-profile", json={"content": ""})
        assert response.status_code == 200
        assert response.json()["content"] is None


class TestAgentsApiDisabled:
    def test_agents_list_returns_403(self, disabled_agent_client):
        response = disabled_agent_client.get("/api/agents")
        assert response.status_code == 403
        assert "agents_api.enabled=true" in response.json()["detail"]

    def test_agent_get_returns_403(self, disabled_agent_client):
        response = disabled_agent_client.get("/api/agents/example-agent")
        assert response.status_code == 403

    def test_agent_name_check_returns_403(self, disabled_agent_client):
        response = disabled_agent_client.get("/api/agents/check", params={"name": "example-agent"})
        assert response.status_code == 403

    def test_agent_create_returns_403(self, disabled_agent_client):
        response = disabled_agent_client.post("/api/agents", json={"name": "example-agent", "soul": "blocked"})
        assert response.status_code == 403

    def test_agent_update_returns_403(self, disabled_agent_client):
        response = disabled_agent_client.put("/api/agents/example-agent", json={"description": "blocked"})
        assert response.status_code == 403

    def test_agent_delete_returns_403(self, disabled_agent_client):
        response = disabled_agent_client.delete("/api/agents/example-agent")
        assert response.status_code == 403

    def test_user_profile_routes_return_403(self, disabled_agent_client):
        get_response = disabled_agent_client.get("/api/user-profile")
        put_response = disabled_agent_client.put("/api/user-profile", json={"content": "blocked"})

        assert get_response.status_code == 403
        assert put_response.status_code == 403
