"""AI-readable harness profile facts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FilesystemPermissionLabel = Literal["read", "write", "execute", "approval_required", "denied"]
AsyncTaskAction = Literal["start", "check", "update", "cancel", "list"]


class ToolCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    source: str
    risk_level: Literal["low", "medium", "high"] = "medium"
    read_only: bool | None = None
    requires_approval: bool = False


class SkillCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    source: str
    enabled: bool = True


class MiddlewareCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    source: str
    protected: bool = False


class FilesystemPermission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: FilesystemPermissionLabel
    scope: str
    enabled: bool = True
    source: str
    details: str | None = None


class AsyncTaskPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions: list[AsyncTaskAction] = Field(default_factory=lambda: ["start", "check", "update", "cancel", "list"])
    no_implicit_polling: bool = True
    source: str = "deerflow.tools.builtins.task_tool"


class ProtectedScaffolding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    reason: str


class AgentHarnessProfile(BaseModel):
    """Strict profile of the harness facts visible to an agent role."""

    model_config = ConfigDict(extra="forbid")

    agent_name: str
    role: str
    tools: list[ToolCapability] = Field(default_factory=list)
    skills: list[SkillCapability] = Field(default_factory=list)
    memory_sources: list[str] = Field(default_factory=list)
    middleware_stack: list[MiddlewareCapability] = Field(default_factory=list)
    filesystem_permissions: list[FilesystemPermission] = Field(default_factory=list)
    async_task_policy: AsyncTaskPolicy = Field(default_factory=AsyncTaskPolicy)
    protected_scaffolding: list[ProtectedScaffolding] = Field(default_factory=list)
