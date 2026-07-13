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
    round_complete: None = None
    next_round_is_safe: None = None
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
            "round_complete": None,
            "next_round_is_safe": None,
            "quality_verdict": None,
            "auto_rework": False,
        }


@dataclass(frozen=True)
class RoundBrief:
    """Objective, compact working memory for the main AI.

    Legacy persisted fields may remain in older records, but are deprecated for
    prompt injection: the model receives facts, not completion or next-step
    guidance.
    """

    goal: str
    boundaries: list[str]
    handoff_signals: list[str]
    open_risks_or_questions: list[str]
    summary: str

    def as_dict(self) -> dict[str, object]:
        return {
            "goal": self.goal,
            "boundaries": list(self.boundaries),
            "handoff_signals": list(self.handoff_signals),
            "open_risks_or_questions": list(self.open_risks_or_questions),
            "summary": self.summary,
        }


def _brief_text(value: str, *, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit].rstrip() if len(text) > limit else text


def create_round_brief(round_: Round) -> RoundBrief:
    """Create compact high-signal working memory from a Round.

    The brief is derived from existing Round/action signals and preserves only
    stated goal, boundaries, occurred actions, and unresolved facts. It does
    not infer evidence gaps, completion, or a next step.
    """

    handoff_signals = []
    for result in round_.action_results[:3]:
        label = _brief_text(result.description or result.action_id, limit=80)
        summary = _brief_text(result.summary, limit=140)
        if label and summary:
            handoff_signals.append(f"{label}: {summary}")
        elif label:
            handoff_signals.append(label)
        elif summary:
            handoff_signals.append(summary)

    open_items = []
    for item in [*round_.risks, *round_.conflicts, *round_.open_questions, *round_.unresolved]:
        text = _brief_text(item)
        if text:
            open_items.append(text)
        if len(open_items) >= 5:
            break

    goal = _brief_text(round_.goal, limit=160)
    boundaries = [_brief_text(item) for item in round_.boundaries[:4] if _brief_text(item)]
    summary_parts = [
        part
        for part in [
            f"Goal: {goal}" if goal else "",
            *(f"Action: {item}" for item in handoff_signals),
            *(f"Unresolved: {item}" for item in open_items),
        ]
        if part
    ]
    return RoundBrief(
        goal=goal,
        boundaries=boundaries,
        handoff_signals=handoff_signals,
        open_risks_or_questions=open_items,
        summary=" | ".join(summary_parts),
    )


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

    Completion, evidence quality, and next-step safety remain AI judgments.
    """

    evidence_signals = round_.evidence_signals()
    needs_user_confirmation = round_.needs_user_confirmation or (round_.next_round is not None and round_.next_round.needs_user_confirmation)
    unresolved = list(round_.unresolved)
    evidence_signals = {
        **evidence_signals,
        "needs_user_confirmation": needs_user_confirmation,
        "requires_confirmation": needs_user_confirmation,
        "unresolved": unresolved,
        "round_complete": None,
        "next_round_is_safe": None,
        "quality_verdict": None,
        "auto_rework": False,
    }
    brief = create_round_brief(round_).as_dict()
    evidence_signals = {
        **evidence_signals,
        "round_brief": brief,
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
        round_complete=None,
        next_round_is_safe=None,
    )


__all__ = [
    "RoundBrief",
    "RoundContextSignals",
    "create_round_brief",
    "create_round_context",
    "extract_action_result",
    "record_action_result_from_event",
    "round_context_signals",
]
