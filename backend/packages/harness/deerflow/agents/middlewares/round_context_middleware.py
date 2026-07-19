"""Inject active Command Room facts into model-call context.

The lead model receives only active native facts, so historic actions cannot
become a soft workflow directive.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

from deerflow.config.app_config import AppConfig

_NATIVE_ROUND_CONTEXT_HEADER = "[Internal Native Round State]"
_CAPABILITY_CONTEXT_HEADER = "[Internal Capability Snapshot]"


def _as_list(value: Any, *, limit: int = 3) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def format_native_round_context_for_model(round_context: Mapping[str, Any] | None) -> str | None:
    """Format only actual native intent and artifact facts for the model."""

    if not round_context:
        return None
    lines: list[str] = []
    intent = round_context.get("current_intent")
    if isinstance(intent, str) and intent.strip():
        lines.append(f"Current user goal: {intent.strip()}")
    parent_intent = round_context.get("parent_intent")
    if isinstance(parent_intent, str) and parent_intent.strip():
        lines.append(f"Previous round user goal (historical fact, not current authorization): {parent_intent.strip()}")
    for key, label in (("artifact_refs", "ArtifactRefs"), ("evidence_refs", "EvidenceRefs")):
        refs = _as_list(round_context.get(key), limit=5)
        if refs:
            lines.append(f"{label}: " + "; ".join(refs))
    if not lines:
        return None
    return "\n".join([_NATIVE_ROUND_CONTEXT_HEADER, "Objective round working memory; do not expose this block verbatim.", *lines])


def format_capability_snapshot_for_model(snapshot: Mapping[str, Any] | None) -> str | None:
    if not snapshot:
        return None

    tool_names: list[str] = []
    for item in snapshot.get("tools") or []:
        if isinstance(item, Mapping):
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                tool_names.append(name.strip())
        if len(tool_names) >= 20:
            break

    approval_policy = snapshot.get("approval_policy") if isinstance(snapshot.get("approval_policy"), Mapping) else {}
    stop_before = _as_list(approval_policy.get("stop_before"), limit=6)
    sandbox = snapshot.get("sandbox") if isinstance(snapshot.get("sandbox"), Mapping) else {}

    lines = [
        _CAPABILITY_CONTEXT_HEADER,
        "Current capability facts only. Do not expose this block verbatim.",
    ]
    if tool_names:
        lines.append("enabled_tools: " + ", ".join(tool_names))
    if stop_before:
        lines.append("stop_before: " + "; ".join(stop_before))
    if sandbox:
        lines.append(
            "sandbox: "
            + "; ".join(
                [
                    f"use={sandbox.get('use')}",
                    f"host_bash_available={bool(sandbox.get('host_bash_available'))}",
                    f"unrestricted_host_access={bool(sandbox.get('unrestricted_host_access'))}",
                ]
            )
        )
    return "\n".join(lines) if len(lines) > 2 else None


class CommandRoomRoundContextMiddleware(AgentMiddleware[AgentState]):
    """Append active-run facts as an internal SystemMessage for Command Room."""

    def __init__(self, *, agent_name: str | None, app_config: AppConfig | None = None):
        self.agent_name = agent_name
        self.app_config = app_config

    def _capability_snapshot(self, thread_id: str | None, user_id: str | None) -> dict[str, Any] | None:
        try:
            from deerflow.capabilities import build_capability_snapshot
            from deerflow.config import get_app_config

            config = self.app_config or get_app_config()
            return build_capability_snapshot(config, thread_id=thread_id, user_id=user_id)
        except Exception:
            return None

    def _capability_text(self, thread_id: str | None, user_id: str | None) -> str | None:
        return format_capability_snapshot_for_model(self._capability_snapshot(thread_id, user_id))

    def _context_text(self, runtime: Runtime | None) -> str | None:
        if self.agent_name != "command-room":
            return None
        ctx = getattr(runtime, "context", None) or {}
        sections: list[str] = []
        native_context = ctx.get("round_context") if isinstance(ctx, Mapping) else None
        if isinstance(native_context, Mapping):
            if native_text := format_native_round_context_for_model(native_context):
                sections.append(native_text)
        return "\n\n".join(sections) if sections else None

    def _inject_text(self, request: ModelRequest, text: str | None) -> ModelRequest:
        if not text:
            return request
        # Avoid duplicate injection on retries or nested middleware passes.
        headers = (_NATIVE_ROUND_CONTEXT_HEADER,)
        if any(isinstance(m, SystemMessage) and isinstance(m.content, str) and any(header in m.content for header in headers) for m in request.messages):
            return request
        msg = SystemMessage(content=text, additional_kwargs={"hide_from_ui": True, "round_context_signals": True})
        return request.override(messages=[msg, *request.messages])

    def _inject(self, request: ModelRequest) -> ModelRequest:
        return self._inject_text(request, self._context_text(request.runtime))

    async def _ainject(self, request: ModelRequest) -> ModelRequest:
        return self._inject_text(request, await asyncio.to_thread(self._context_text, request.runtime))

    @override
    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelCallResult:
        return handler(self._inject(request))

    @override
    async def awrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]) -> ModelCallResult:
        return await handler(await self._ainject(request))


__all__ = [
    "CommandRoomRoundContextMiddleware",
    "format_capability_snapshot_for_model",
    "format_native_round_context_for_model",
]
