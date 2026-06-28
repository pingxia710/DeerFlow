"""Inject compact Command Room Round signals into model-call context.

The middleware is intentionally read-only and advisory: it does not make
PASS/FAIL decisions, trigger rework, dispatch reviewers, or change visible
responses.  It only exposes the latest persisted Command Room RoundRecord's
roundContextSignals to the main model as short internal context.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

from deerflow.runtime.user_context import get_effective_user_id

_INTERNAL_CONTEXT_HEADER = "[Internal Command Room Round signals]"


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


def format_round_context_for_model(record: Mapping[str, Any] | None) -> str | None:
    """Return a short advisory context block for the model, or None.

    Only records with ``roundRequired=True`` and non-empty
    ``roundContextSignals`` are injected.  Raw sub-agent output is deliberately
    excluded; only compact mechanical signals are surfaced.
    """

    if not record or record.get("roundRequired") is not True:
        return None
    signals = record.get("roundContextSignals")
    if not isinstance(signals, Mapping):
        return None

    evidence = signals.get("evidence_signals") if isinstance(signals.get("evidence_signals"), Mapping) else {}
    lines = [
        _INTERNAL_CONTEXT_HEADER,
        "These are advisory signals from the previous/current RoundRecord, not a verdict. The main AI must judge next steps; do not expose this block verbatim.",
        f"round_complete={bool(signals.get('round_complete'))}; next_round_is_safe={bool(signals.get('next_round_is_safe'))}; needs_user_confirmation={bool(signals.get('needs_user_confirmation') or signals.get('requires_confirmation'))}",
        f"actions={signals.get('action_count', 0)}; evidence={evidence.get('evidence_state') or evidence.get('state') or evidence.get('summary') or signals.get('summary') or 'available'}",
    ]
    for key, label in (("risks", "risks"), ("unresolved", "unresolved"), ("open_questions", "open_questions"), ("conflicts", "conflicts")):
        values = _as_list(signals.get(key))
        if values:
            lines.append(f"{label}: " + "; ".join(values))
    return "\n".join(lines)


def latest_round_context_for_thread(thread_id: str | None, user_id: str | None = None) -> str | None:
    if not thread_id:
        return None
    from deerflow.command_room.round_record import latest_command_room_round

    return format_round_context_for_model(latest_command_room_round(thread_id=thread_id, user_id=user_id))


class CommandRoomRoundContextMiddleware(AgentMiddleware[AgentState]):
    """Append latest Round signals as an internal SystemMessage for Command Room."""

    def __init__(self, *, agent_name: str | None):
        self.agent_name = agent_name

    def _context_text(self, runtime: Runtime | None) -> str | None:
        if self.agent_name != "command-room":
            return None
        ctx = getattr(runtime, "context", None) or {}
        thread_id = ctx.get("thread_id") if isinstance(ctx, Mapping) else None
        return latest_round_context_for_thread(str(thread_id) if thread_id else None, get_effective_user_id())

    def _inject(self, request: ModelRequest) -> ModelRequest:
        text = self._context_text(request.runtime)
        if not text:
            return request
        # Avoid duplicate injection on retries or nested middleware passes.
        if any(isinstance(m, SystemMessage) and isinstance(m.content, str) and _INTERNAL_CONTEXT_HEADER in m.content for m in request.messages):
            return request
        msg = SystemMessage(content=text, additional_kwargs={"hide_from_ui": True, "round_context_signals": True})
        return request.override(messages=[msg, *request.messages])

    @override
    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelCallResult:
        return handler(self._inject(request))

    @override
    async def awrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]) -> ModelCallResult:
        return await handler(self._inject(request))


__all__ = ["CommandRoomRoundContextMiddleware", "format_round_context_for_model", "latest_round_context_for_thread"]
