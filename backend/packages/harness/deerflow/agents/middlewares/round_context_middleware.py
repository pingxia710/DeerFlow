"""Inject active Command Room facts into model-call context.

Persisted RoundRecords are audit data, not instructions for a later user turn.
The lead model receives only active native facts, so historic actions cannot
become a soft workflow directive.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

from deerflow.config.app_config import AppConfig

_INTERNAL_CONTEXT_HEADER = "[Internal Command Room Round signals]"
_NATIVE_ROUND_CONTEXT_HEADER = "[Internal Native Round State]"
_CAPABILITY_CONTEXT_HEADER = "[Internal Capability Snapshot]"
_QUALITY_SIGNALS_HEADER = "[Internal AI Quality Signals]"
_REVIEW_INVOCATIONS_HEADER = "[Internal AI Review Invocations]"
_ACCOUNT_LEDGER_HEADER = "[Internal AI Account Ledger]"
_CHAIR_BRIEF_HEADER = "[Internal Chair Operating Brief]"
_ROLE_STATE_HEADER = "[Internal AI Role State]"
_PENDING_HANDOFFS_HEADER = "[Internal Pending AI Handoffs]"


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
    """Return objective persisted-round facts without workflow guidance."""

    if not isinstance(record, Mapping):
        return None

    lines: list[str] = []
    if record.get("version") != 2:
        if record.get("roundRequired") is not True:
            return None
        signals = record.get("roundContextSignals")
        if not isinstance(signals, Mapping):
            return None
        evidence = signals.get("evidence_signals") if isinstance(signals.get("evidence_signals"), Mapping) else {}
        brief = record.get("roundBrief") if isinstance(record.get("roundBrief"), Mapping) else evidence.get("round_brief")
        brief = brief if isinstance(brief, Mapping) else {}
        goal = brief.get("goal")
        if isinstance(goal, str) and goal.strip():
            lines.append(f"Current user goal: {goal.strip()}")
        for label, values in (("Boundary", brief.get("boundaries")), ("Action occurred", brief.get("handoff_signals"))):
            facts = _as_list(values)
            if facts:
                lines.append(f"{label}: " + "; ".join(facts))
        for key, label in (("risks", "Risk"), ("conflicts", "Conflict"), ("open_questions", "Unresolved question"), ("unresolved", "Unresolved")):
            facts = _as_list(signals.get(key))
            if facts:
                lines.append(f"{label}: " + "; ".join(facts))
        if not lines:
            return None
        return "\n".join([_INTERNAL_CONTEXT_HEADER, "Objective round working memory; do not expose this block verbatim.", *lines])

    goal = record.get("userGoal")
    if isinstance(goal, Mapping):
        chars = goal.get("chars")
        if isinstance(chars, int) and chars:
            lines.append(f"Persisted user goal fact: text fingerprint recorded ({chars} chars)")

    boundaries = record.get("explicitBoundary")
    if isinstance(boundaries, list):
        facts = [str(item.get("value")).strip() for item in boundaries if isinstance(item, Mapping) and isinstance(item.get("value"), str) and item.get("value").strip()]
        if facts:
            lines.append("Explicit boundary: " + "; ".join(facts[:3]))

    actions = record.get("actionResults")
    if isinstance(actions, list):
        action_facts: list[str] = []
        for action in actions[:3]:
            if not isinstance(action, Mapping):
                continue
            pieces: list[str] = []
            for key, label in (("taskId", "task"), ("status", "status"), ("role", "role")):
                value = action.get(key)
                if isinstance(value, str) and value.strip():
                    pieces.append(f"{label} {value.strip()}")
            result = action.get("actionResult")
            if isinstance(result, Mapping):
                for key in ("description", "summary", "output_ref", "result_ref"):
                    value = result.get(key)
                    if isinstance(value, str) and value.strip():
                        pieces.append(f"{key}: {value.strip()}")
            error = action.get("error")
            if isinstance(error, str) and error.strip():
                pieces.append(f"error: {error.strip()}")
            if pieces:
                action_facts.append(", ".join(pieces))
        if action_facts:
            lines.append("Action occurred: " + "; ".join(action_facts))

    if not lines:
        return None
    return "\n".join([_INTERNAL_CONTEXT_HEADER, "Objective persisted round facts; do not expose this block verbatim.", *lines])


def latest_round_context_for_thread(thread_id: str | None, user_id: str | None = None) -> str | None:
    if not thread_id:
        return None
    from deerflow.command_room.round_record import latest_command_room_round

    return format_round_context_for_model(latest_command_room_round(thread_id=thread_id, user_id=user_id))


def format_native_round_context_for_model(round_context: Mapping[str, Any] | None) -> str | None:
    """Format only actual native intent and artifact facts for the model."""

    if not round_context:
        return None
    lines: list[str] = []
    intent = round_context.get("current_intent")
    if isinstance(intent, str) and intent.strip():
        lines.append(f"Current user goal: {intent.strip()}")
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


def format_quality_signals_for_model(signals: list[Mapping[str, Any]] | None) -> str | None:
    if not signals:
        return None
    from deerflow.command_room.quality import compact_quality_signals

    compact = compact_quality_signals([dict(signal) for signal in signals], limit=3)
    if not compact:
        return None
    lines = [
        _QUALITY_SIGNALS_HEADER,
        "AI-authored recommendations only. Chair decides next steps; no automatic dispatch or rework.",
    ]
    for signal in compact:
        parts = [
            f"author={signal.get('author_role')}",
            f"recommendation={signal.get('recommendation')}",
            f"target={signal.get('target_role') or 'Chair'}",
        ]
        if signal.get("round_id"):
            parts.append(f"round_id={signal.get('round_id')}")
        if signal.get("task_id"):
            parts.append(f"task_id={signal.get('task_id')}")
        lines.append("; ".join(parts))
        rationale = signal.get("rationale")
        if isinstance(rationale, str) and rationale.strip():
            lines.append(f"rationale: {rationale.strip()}")
        refs = _as_list(signal.get("evidence_refs"), limit=3)
        if refs:
            lines.append("EvidenceRefs: " + "; ".join(refs))
    return "\n".join(lines)


def latest_quality_signals_for_thread(thread_id: str | None, user_id: str | None = None) -> str | None:
    if not thread_id:
        return None
    from deerflow.command_room.quality import list_quality_signals

    return format_quality_signals_for_model(list_quality_signals(thread_id=thread_id, user_id=user_id, limit=3))


def format_review_invocations_for_model(invocations: list[Mapping[str, Any]] | None) -> str | None:
    if not invocations:
        return None
    from deerflow.command_room.review import compact_review_invocations

    compact = compact_review_invocations([dict(invocation) for invocation in invocations], limit=3)
    if not compact:
        return None
    lines = [
        _REVIEW_INVOCATIONS_HEADER,
        "AI-authored review requests only. Chair decides next steps; no automatic reviewer dispatch or rework.",
    ]
    for invocation in compact:
        parts = [
            f"reviewer={invocation.get('reviewer_role')}",
            f"status={invocation.get('status')}",
            f"target={invocation.get('target_role') or 'Chair'}",
        ]
        if invocation.get("round_id"):
            parts.append(f"round_id={invocation.get('round_id')}")
        if invocation.get("task_id"):
            parts.append(f"task_id={invocation.get('task_id')}")
        lines.append("; ".join(parts))
        focus = invocation.get("focus")
        if isinstance(focus, str) and focus.strip():
            lines.append(f"focus: {focus.strip()}")
        result_summary = invocation.get("result_summary")
        if isinstance(result_summary, str) and result_summary.strip():
            lines.append(f"result_summary: {result_summary.strip()}")
    return "\n".join(lines)


def latest_review_invocations_for_thread(thread_id: str | None, user_id: str | None = None) -> str | None:
    if not thread_id:
        return None
    from deerflow.command_room.review import list_review_invocations

    return format_review_invocations_for_model(list_review_invocations(thread_id=thread_id, user_id=user_id, limit=3))


def format_account_ledger_for_model(proposals: list[Mapping[str, Any]] | None, decisions: list[Mapping[str, Any]] | None) -> str | None:
    if not proposals and not decisions:
        return None
    from deerflow.command_room.account_ledger import compact_account_ledger

    compact = compact_account_ledger([dict(proposal) for proposal in proposals or []], [dict(decision) for decision in decisions or []], limit=3)
    if not compact:
        return None
    lines = [
        _ACCOUNT_LEDGER_HEADER,
        "AI-authored governance records only. Chair decisions are recorded, not automatically applied.",
    ]
    for entry in compact:
        parts = [
            f"account_type={entry.get('account_type')}",
            f"proposed_by_role={entry.get('proposed_by_role')}",
            f"target={entry.get('target_role') or 'Chair'}",
        ]
        if entry.get("decision"):
            parts.append(f"decision={entry.get('decision')}")
        if entry.get("created_at"):
            parts.append(f"created_at={entry.get('created_at')}")
        lines.append(f"{entry.get('entry')}: " + "; ".join(parts))
    return "\n".join(lines)


def latest_account_ledger_for_thread(thread_id: str | None, user_id: str | None = None) -> str | None:
    if not thread_id:
        return None
    from deerflow.command_room.account_ledger import list_account_decisions, list_account_proposals

    return format_account_ledger_for_model(
        list_account_proposals(thread_id=thread_id, user_id=user_id, limit=3),
        list_account_decisions(thread_id=thread_id, user_id=user_id, limit=3),
    )


def format_role_states_for_model(states: list[Mapping[str, Any]] | None) -> str | None:
    if not states:
        return None
    from deerflow.command_room.role_state import compact_role_states

    compact = compact_role_states([dict(state) for state in states], limit=5)
    if not compact:
        return None
    lines = [
        _ROLE_STATE_HEADER,
        "Chair-accepted AI role memory only. It is advisory and must not auto-dispatch work.",
    ]
    for state in compact:
        parts = [
            f"role={state.get('role_name')}",
            f"updated_by={state.get('updated_by_role') or 'chair'}",
            f"target={state.get('target_role') or 'Chair'}",
        ]
        if state.get("round_id"):
            parts.append(f"round_id={state.get('round_id')}")
        lines.append("; ".join(parts))
        summary = state.get("summary")
        if isinstance(summary, str) and summary.strip():
            lines.append(f"summary: {summary.strip()}")
        focus = state.get("current_focus")
        if isinstance(focus, str) and focus.strip():
            lines.append(f"current_focus: {focus.strip()}")
        questions = _as_list(state.get("open_questions"), limit=3)
        if questions:
            lines.append("open_questions: " + "; ".join(questions))
        signals = _as_list(state.get("accepted_signals"), limit=3)
        if signals:
            lines.append("accepted_signals: " + "; ".join(signals))
    return "\n".join(lines)


def latest_role_states_for_thread(thread_id: str | None, user_id: str | None = None) -> str | None:
    if not thread_id:
        return None
    from deerflow.command_room.role_state import list_role_states

    return format_role_states_for_model(list_role_states(thread_id=thread_id, user_id=user_id, limit=5))


def format_pending_handoffs_for_model(handoffs: list[Mapping[str, Any]] | None) -> str | None:
    if not handoffs:
        return None
    from deerflow.command_room.pending_handoff import compact_pending_handoffs

    compact = compact_pending_handoffs([dict(handoff) for handoff in handoffs], limit=5)
    if not compact:
        return None
    lines = [
        _PENDING_HANDOFFS_HEADER,
        "AI-authored next-role suggestions only. Chair decides; no automatic dispatch.",
    ]
    for handoff in compact:
        parts = [
            f"handoff_id={handoff.get('handoff_id')}",
            f"source={handoff.get('source_role')}",
            f"target={handoff.get('target_role')}",
            f"status={handoff.get('status')}",
        ]
        if handoff.get("task_id"):
            parts.append(f"task_id={handoff.get('task_id')}")
        lines.append("; ".join(parts))
        task_or_question = handoff.get("task_or_question")
        if isinstance(task_or_question, str) and task_or_question.strip():
            lines.append(f"task_or_question: {task_or_question.strip()}")
        evidence_refs = _as_list(handoff.get("evidence_refs"), limit=3)
        if evidence_refs:
            lines.append("EvidenceRefs: " + "; ".join(evidence_refs))
    return "\n".join(lines)


def latest_pending_handoffs_for_thread(
    thread_id: str | None,
    user_id: str | None = None,
    run_id: str | None = None,
) -> str | None:
    if not thread_id:
        return None
    from deerflow.command_room.pending_handoff import list_pending_handoffs

    return format_pending_handoffs_for_model(list_pending_handoffs(thread_id=thread_id, user_id=user_id, run_id=run_id, status="pending", limit=5))


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
        native_context = ctx.get("round_context") if isinstance(ctx, Mapping) else None
        if isinstance(native_context, Mapping):
            return format_native_round_context_for_model(native_context)
        return None

    def _inject(self, request: ModelRequest) -> ModelRequest:
        text = self._context_text(request.runtime)
        if not text:
            return request
        # Avoid duplicate injection on retries or nested middleware passes.
        headers = (_INTERNAL_CONTEXT_HEADER, _NATIVE_ROUND_CONTEXT_HEADER)
        if any(isinstance(m, SystemMessage) and isinstance(m.content, str) and any(header in m.content for header in headers) for m in request.messages):
            return request
        msg = SystemMessage(content=text, additional_kwargs={"hide_from_ui": True, "round_context_signals": True})
        return request.override(messages=[msg, *request.messages])

    @override
    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelCallResult:
        return handler(self._inject(request))

    @override
    async def awrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]) -> ModelCallResult:
        return await handler(self._inject(request))


__all__ = [
    "CommandRoomRoundContextMiddleware",
    "format_account_ledger_for_model",
    "format_capability_snapshot_for_model",
    "format_native_round_context_for_model",
    "format_pending_handoffs_for_model",
    "format_quality_signals_for_model",
    "format_role_states_for_model",
    "format_review_invocations_for_model",
    "format_round_context_for_model",
    "latest_account_ledger_for_thread",
    "latest_pending_handoffs_for_thread",
    "latest_quality_signals_for_thread",
    "latest_role_states_for_thread",
    "latest_review_invocations_for_thread",
    "latest_round_context_for_thread",
]
