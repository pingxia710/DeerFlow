"""Lightweight bridge for collecting task action results into a Command Room round.

This module is intentionally side-effect free.  It does not alter task() return
values, dispatch reviewers, make quality verdicts, or trigger rework.  Callers
may opt in by creating a round context and feeding terminal task event metadata
into it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .action_result_adapter import action_result_from_value
from .round import Round, summarize_round


@dataclass(frozen=True)
class RoundContextSignals:
    """Small summary intended for the main AI / Command Room."""

    action_count: int
    risks: list[str]
    conflicts: list[str]
    open_questions: list[str]
    unresolved: list[str]
    evidence_signals: dict[str, object]
    summary: str
    needs_user_confirmation: bool = False
    requires_confirmation: bool = False
    round_complete: bool = False
    next_round_is_safe: bool = True
    quality_verdict: None = None
    auto_rework: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "action_count": self.action_count,
            "risks": list(self.risks),
            "conflicts": list(self.conflicts),
            "open_questions": list(self.open_questions),
            "unresolved": list(self.unresolved),
            "evidence_signals": self.evidence_signals,
            "summary": self.summary,
            "needs_user_confirmation": self.needs_user_confirmation,
            "requires_confirmation": self.requires_confirmation,
            "round_complete": self.round_complete,
            "next_round_is_safe": self.next_round_is_safe,
            "quality_verdict": None,
            "auto_rework": False,
        }


def create_round_context(
    goal: str,
    *,
    boundaries: list[str] | None = None,
    known_facts: list[str] | None = None,
    open_questions: list[str] | None = None,
    allowed_actions: list[str] | None = None,
    evidence_standard: list[str] | None = None,
) -> Round:
    """Create an opt-in current round without touching runtime task behavior."""

    return Round(
        goal=goal,
        boundaries=list(boundaries or []),
        known_facts=list(known_facts or []),
        open_questions=list(open_questions or []),
        allowed_actions=list(allowed_actions or []),
        evidence_standard=list(evidence_standard or []),
    )


def record_action_result_from_event(round_: Round, event: Mapping[str, Any]) -> Round:
    """Record ``event['action_result']`` into ``round_`` when present.

    Events without action_result are ignored and return the original round.  The
    function accepts the terminal task event dict written by task_tool, but does
    not require or modify task_tool itself.
    """

    action_result = extract_action_result(event)
    if action_result is None:
        return round_
    default_action_id = str(event.get("task_id") or event.get("id") or "")
    return round_.record_action_result(action_result_from_value(action_result, default_action_id=default_action_id))


def extract_action_result(event_or_metadata: Mapping[str, Any] | None) -> Any | None:
    """Extract action_result from an event/metadata mapping, if available."""

    if not event_or_metadata:
        return None
    if "action_result" in event_or_metadata:
        return event_or_metadata.get("action_result")
    metadata = event_or_metadata.get("metadata")
    if isinstance(metadata, Mapping):
        return metadata.get("action_result")
    return None


def round_context_signals(round_: Round) -> RoundContextSignals:
    """Build lightweight mechanical signals for the main AI.

    ``quality_verdict`` is deliberately always None and ``auto_rework`` is
    deliberately always False; evidence strength is only a hint.
    """

    evidence_signals = round_.evidence_signals()
    needs_user_confirmation = round_.needs_user_confirmation or (round_.next_round is not None and round_.next_round.needs_user_confirmation)
    unresolved = list(round_.unresolved)
    if needs_user_confirmation and not any(item.strip() for item in unresolved):
        unresolved.append("requires user confirmation before next step")
    evidence_signals = {
        **evidence_signals,
        "needs_user_confirmation": needs_user_confirmation,
        "requires_confirmation": needs_user_confirmation,
        "unresolved": unresolved,
        "round_complete": round_.is_complete,
        "next_round_is_safe": round_.next_round_is_safe,
        "quality_verdict": None,
        "auto_rework": False,
    }
    return RoundContextSignals(
        action_count=len(round_.action_results),
        risks=list(round_.risks),
        conflicts=list(round_.conflicts),
        open_questions=list(round_.open_questions),
        unresolved=unresolved,
        evidence_signals=evidence_signals,
        summary=summarize_round(round_),
        needs_user_confirmation=needs_user_confirmation,
        requires_confirmation=needs_user_confirmation,
        round_complete=round_.is_complete,
        next_round_is_safe=round_.next_round_is_safe,
    )


__all__ = [
    "RoundContextSignals",
    "create_round_context",
    "extract_action_result",
    "record_action_result_from_event",
    "round_context_signals",
]
