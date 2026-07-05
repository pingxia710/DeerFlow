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

from deerflow.config.app_config import AppConfig
from deerflow.runtime.user_context import get_effective_user_id

_INTERNAL_CONTEXT_HEADER = "[Internal Command Room Round signals]"
_NATIVE_ROUND_CONTEXT_HEADER = "[Internal Native Round State]"
_CAPABILITY_CONTEXT_HEADER = "[Internal Capability Snapshot]"
_QUALITY_SIGNALS_HEADER = "[Internal AI Quality Signals]"
_REVIEW_INVOCATIONS_HEADER = "[Internal AI Review Invocations]"
_ACCOUNT_LEDGER_HEADER = "[Internal AI Account Ledger]"
_CHAIR_BRIEF_HEADER = "[Internal Chair Operating Brief]"


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
    brief = record.get("roundBrief") if isinstance(record.get("roundBrief"), Mapping) else evidence.get("round_brief")
    brief = brief if isinstance(brief, Mapping) else {}
    lines = [
        _INTERNAL_CONTEXT_HEADER,
        "These are advisory signals from the previous/current RoundRecord, not a verdict. The main AI must judge next steps; do not expose this block verbatim.",
    ]
    if brief:
        if brief.get("summary"):
            lines.append(f"brief: {brief.get('summary')}")
        if brief.get("evidence_status"):
            lines.append(f"evidence_status: {brief.get('evidence_status')}")
        if brief.get("next_safe_action"):
            lines.append(f"next_safe_action: {brief.get('next_safe_action')}")
    round_complete = bool(signals.get("round_complete"))
    next_round_is_safe = bool(signals.get("next_round_is_safe"))
    needs_user_confirmation = bool(signals.get("needs_user_confirmation") or signals.get("requires_confirmation"))
    evidence_summary = evidence.get("evidence_state") or evidence.get("state") or evidence.get("summary") or signals.get("summary") or "available"
    status_line = "; ".join(
        [
            f"round_complete={round_complete}",
            f"next_round_is_safe={next_round_is_safe}",
            f"needs_user_confirmation={needs_user_confirmation}",
        ]
    )
    lines.extend(
        [
            status_line,
            f"actions={signals.get('action_count', 0)}; evidence={evidence_summary}",
        ]
    )
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


def format_native_round_context_for_model(round_context: Mapping[str, Any] | None) -> str | None:
    if not round_context:
        return None
    lines = [
        _NATIVE_ROUND_CONTEXT_HEADER,
        "Mechanical lifecycle state only. Do not treat this as quality judgment or expose it verbatim.",
    ]
    for key, label in (
        ("round_id", "round_id"),
        ("state", "state"),
        ("current_run_id", "current_run_id"),
        ("source_goal_run_id", "source_goal_run_id"),
        ("parent_round_id", "parent_round_id"),
        ("current_intent", "Current Intent"),
        ("accepted_next_action", "Accepted Next Action"),
    ):
        value = round_context.get(key)
        if isinstance(value, str) and value.strip():
            lines.append(f"{label}: {value.strip()}")
    for key, label in (("artifact_refs", "ArtifactRefs"), ("evidence_refs", "EvidenceRefs")):
        refs = _as_list(round_context.get(key), limit=5)
        if refs:
            lines.append(f"{label}: " + "; ".join(refs))
    return "\n".join(lines) if len(lines) > 2 else None


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


class CommandRoomRoundContextMiddleware(AgentMiddleware[AgentState]):
    """Append latest Round signals as an internal SystemMessage for Command Room."""

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
        thread_id = ctx.get("thread_id") if isinstance(ctx, Mapping) else None
        user_id = get_effective_user_id()
        thread_id_text = str(thread_id) if thread_id else None
        native_context = ctx.get("round_context") if isinstance(ctx, Mapping) else None
        current_run_id = native_context.get("current_run_id") if isinstance(native_context, Mapping) else None
        round_id = native_context.get("round_id") if isinstance(native_context, Mapping) else None
        native_evidence_refs = _as_list(native_context.get("evidence_refs"), limit=20) if isinstance(native_context, Mapping) else []
        snapshot = self._capability_snapshot(thread_id_text, user_id)
        from deerflow.command_room.account_ledger import list_account_decisions, list_account_proposals
        from deerflow.command_room.brief import build_chair_operating_brief, format_chair_operating_brief_for_model
        from deerflow.command_room.quality import list_quality_signals
        from deerflow.command_room.review import list_review_invocations

        quality_rows = list_quality_signals(thread_id=thread_id_text, user_id=user_id, run_id=str(current_run_id) if current_run_id else None, limit=3) if thread_id_text else []
        review_rows = list_review_invocations(thread_id=thread_id_text, user_id=user_id, run_id=str(current_run_id) if current_run_id else None, limit=3) if thread_id_text else []
        proposal_rows = list_account_proposals(thread_id=thread_id_text, user_id=user_id, run_id=str(current_run_id) if current_run_id else None, limit=3) if thread_id_text else []
        decision_rows = list_account_decisions(thread_id=thread_id_text, user_id=user_id, run_id=str(current_run_id) if current_run_id else None, limit=3) if thread_id_text else []
        parts: list[str] = []
        if thread_id_text:
            brief = build_chair_operating_brief(
                thread_id=thread_id_text,
                run_id=str(current_run_id or ""),
                round_id=str(round_id) if round_id else None,
                capability_snapshot=snapshot,
                evidence={"evidence_refs": [{"ref": ref, "round_id": str(round_id) if round_id else None} for ref in native_evidence_refs]},
                quality_signals=quality_rows,
                review_invocations=review_rows,
                account_proposals=proposal_rows,
                account_decisions=decision_rows,
            )
            brief_text = format_chair_operating_brief_for_model(brief)
            if brief_text:
                parts.append(brief_text)
        capability_text = format_capability_snapshot_for_model(snapshot)
        if capability_text:
            parts.append(capability_text)
        if isinstance(native_context, Mapping):
            text = format_native_round_context_for_model(native_context)
            if text:
                parts.append(text)
        quality_text = format_quality_signals_for_model(quality_rows)
        if quality_text:
            parts.append(quality_text)
        review_text = format_review_invocations_for_model(review_rows)
        if review_text:
            parts.append(review_text)
        account_text = format_account_ledger_for_model(proposal_rows, decision_rows)
        if account_text:
            parts.append(account_text)
        legacy_text = latest_round_context_for_thread(thread_id_text, user_id)
        if legacy_text:
            parts.append(legacy_text)
        return "\n\n".join(parts) if parts else None

    def _inject(self, request: ModelRequest) -> ModelRequest:
        text = self._context_text(request.runtime)
        if not text:
            return request
        # Avoid duplicate injection on retries or nested middleware passes.
        headers = (
            _INTERNAL_CONTEXT_HEADER,
            _NATIVE_ROUND_CONTEXT_HEADER,
            _CAPABILITY_CONTEXT_HEADER,
            _QUALITY_SIGNALS_HEADER,
            _REVIEW_INVOCATIONS_HEADER,
            _ACCOUNT_LEDGER_HEADER,
            _CHAIR_BRIEF_HEADER,
        )
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
    "format_quality_signals_for_model",
    "format_review_invocations_for_model",
    "format_round_context_for_model",
    "latest_account_ledger_for_thread",
    "latest_quality_signals_for_thread",
    "latest_review_invocations_for_thread",
    "latest_round_context_for_thread",
]
