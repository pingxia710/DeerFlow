from __future__ import annotations

import pytest
from pydantic import ValidationError

from deerflow.capabilities import (
    AgentHarnessProfile,
    AsyncTaskPolicy,
    FilesystemPermission,
    MiddlewareCapability,
    ProtectedScaffolding,
    SkillCapability,
    ToolCapability,
)


def test_agent_harness_profile_exposes_strict_command_room_facts():
    profile = AgentHarnessProfile(
        agent_name="command-room",
        role="chair",
        tools=[ToolCapability(name="read_file", source="config.tools", risk_level="low", read_only=True)],
        skills=[SkillCapability(name="command-room-chair", source="public", enabled=True)],
        memory_sources=["user", "agent", "thread"],
        middleware_stack=[MiddlewareCapability(name="CommandRoomRoundContextMiddleware", source="lead_agent.build_middlewares", protected=True)],
        filesystem_permissions=[
            FilesystemPermission(label="read", scope="thread user-data", source="ThreadDataMiddleware"),
            FilesystemPermission(label="write", scope="thread outputs", source="sandbox file tools"),
            FilesystemPermission(label="execute", scope="sandbox bash", enabled=False, source="sandbox config"),
            FilesystemPermission(label="approval_required", scope="dangerous host mounts", source="sandbox config"),
            FilesystemPermission(label="denied", scope="arbitrary host paths", source="sandbox config"),
        ],
        async_task_policy=AsyncTaskPolicy(),
        protected_scaffolding=[ProtectedScaffolding(name="SandboxMiddleware", reason="core safety/context middleware")],
    )

    assert profile.agent_name == "command-room"
    assert profile.async_task_policy.actions == ["start", "check", "update", "cancel", "list"]
    assert profile.async_task_policy.no_implicit_polling is True
    assert {item.label for item in profile.filesystem_permissions} == {"read", "write", "execute", "approval_required", "denied"}


def test_agent_harness_profile_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        AgentHarnessProfile(agent_name="worker", role="subagent", extra_field=True)
