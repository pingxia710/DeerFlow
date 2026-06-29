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

_FORBIDDEN_BRIEF_TERMS = ("gate", "verdict", "pass", "fail")


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


@dataclass(frozen=True)
class RoundBrief:
    """Short internal working-memory brief for the main AI.

    This is advisory context only. It is intentionally not a workflow decision,
    quality judgement, or automatic rework trigger.
    """

    goal: str
    boundaries: list[str]
    handoff_signals: list[str]
    evidence_status: str
    open_risks_or_questions: list[str]
    next_safe_action: str
    summary: str

    def as_dict(self) -> dict[str, object]:
        return {
            "goal": self.goal,
            "boundaries": list(self.boundaries),
            "handoff_signals": list(self.handoff_signals),
            "evidence_status": self.evidence_status,
            "open_risks_or_questions": list(self.open_risks_or_questions),
            "next_safe_action": self.next_safe_action,
            "summary": self.summary,
        }


def _brief_text(value: str, *, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    lowered = text.lower()
    for term in _FORBIDDEN_BRIEF_TERMS:
        lowered = lowered.replace(term, "")
    text = lowered.strip(" :-;,.，。") if lowered != text.lower() else text
    return text[:limit].rstrip() if len(text) > limit else text


def create_round_brief(round_: Round) -> RoundBrief:
    """Create compact high-signal working memory from a Round.

    The brief is derived from existing Round/action/evidence signals and keeps
    only useful next-step context. It deliberately avoids gate/verdict/pass/fail
    semantics so the lead AI remains responsible for judgement.
    """

    evidence = round_.evidence_signals()
    strong = int(evidence.get("strong_count") or 0)
    weak = int(evidence.get("weak_count") or 0)
    if strong:
        evidence_status = f"{strong} trusted observable evidence signal(s)"
    elif weak:
        evidence_status = f"{weak} weak evidence signal(s); treat worker self-claims as untrusted"
    else:
        evidence_status = "no observable evidence signal yet"

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

    next_safe_action = _brief_text(round_.next_step, limit=180)
    if not next_safe_action and round_.next_round is not None and round_.next_round.within_current_boundary and not round_.next_round.needs_user_confirmation:
        next_safe_action = _brief_text(round_.next_round.proposal, limit=180)
    if not next_safe_action:
        next_safe_action = "continue only inside stated boundaries; ask user before new authorization or redline changes"

    summary_parts = [
        part
        for part in [
            f"Goal: {_brief_text(round_.goal, limit=160)}" if round_.goal.strip() else "",
            f"Evidence: {evidence_status}",
            f"Next safe action: {next_safe_action}",
        ]
        if part
    ]
    return RoundBrief(
        goal=_brief_text(round_.goal, limit=160),
        boundaries=[_brief_text(item) for item in round_.boundaries[:4] if _brief_text(item)],
        handoff_signals=handoff_signals,
        evidence_status=evidence_status,
        open_risks_or_questions=open_items,
        next_safe_action=next_safe_action,
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
        round_complete=round_.is_complete,
        next_round_is_safe=round_.next_round_is_safe,
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
