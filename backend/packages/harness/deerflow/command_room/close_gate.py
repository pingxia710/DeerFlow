"""Read-only Round Close Gate fact report helpers.

Close Gate reports summarize observable Command Room facts only. They never
produce PASS/FAIL verdicts, dispatch work, or mutate round/run/task state.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from .evidence import analyze_evidence_ref
from .lane_reconciliation import build_lane_reconciliation_facts
from .round_lifecycle import build_round_lifecycle_hint

_ACTIVE_LANE_STATUSES = {"planned", "dispatched", "running"}
_FAILED_OR_BLOCKED_STATUSES = {"failed", "blocked"}
_OPEN_REVIEW_STATUSES = {"requested"}
_OPEN_HANDOFF_STATUSES = {"pending", "open", "requested"}
_STRONG_STRENGTHS = {"strong"}
_WEAK_STRENGTHS = {"weak", "unverified", "unknown"}


@dataclass(frozen=True)
class CloseGateReport:
    thread_id: str
    run_id: str
    round_id: str | None
    open_pending_handoffs: list[dict[str, Any]] = field(default_factory=list)
    active_planned_lanes: list[dict[str, Any]] = field(default_factory=list)
    active_task_lanes: list[dict[str, Any]] = field(default_factory=list)
    failed_or_blocked_lanes: list[dict[str, Any]] = field(default_factory=list)
    open_review_invocations: list[dict[str, Any]] = field(default_factory=list)
    quality_recommendations: dict[str, int] = field(default_factory=dict)
    quality_signal_summary: dict[str, Any] = field(default_factory=dict)
    evidence_summary: dict[str, Any] = field(default_factory=dict)
    facts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    next_check_hint: str | None = None
    round_lifecycle_hint: dict[str, Any] = field(default_factory=dict)
    lane_reconciliation: dict[str, Any] = field(default_factory=dict)
    programmatic_decision: bool = False
    auto_dispatch: bool = False
    quality_verdict: None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, "as_dict"):
        value = row.as_dict()
        return dict(value) if isinstance(value, dict) else {}
    if hasattr(row, "__dict__"):
        return dict(row.__dict__)
    return {}


def _matches_round(row: dict[str, Any], round_id: str | None) -> bool:
    return round_id is None or row.get("round_id") in {None, "", round_id}


def _status(row: dict[str, Any]) -> str:
    return str(row.get("status") or "").strip().lower()


def _row_id(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value:
            return str(value)
    return None


def _summarize_evidence(evidence_refs: list[Any]) -> dict[str, Any]:
    refs = [_as_dict(ref) if not isinstance(ref, str) else {"ref": ref} for ref in evidence_refs]
    strengths: Counter[str] = Counter()
    strong_refs: list[str] = []
    weak_or_unverified_refs: list[str] = []
    unknown_refs: list[str] = []
    for ref in refs:
        raw_strength = str(ref.get("strength") or "").strip().lower()
        text = str(ref.get("ref") or ref.get("claim") or ref.get("ref_id") or "").strip()
        if raw_strength:
            strength = raw_strength
            strong = strength in _STRONG_STRENGTHS
        else:
            signal = analyze_evidence_ref(text)
            strength = "strong" if signal.strong else "weak" if signal.weak_reasons else "unverified"
            strong = signal.strong
        strengths[strength] += 1
        label = str(ref.get("ref_id") or text or "<empty>")
        if strong:
            strong_refs.append(label)
        elif strength in _WEAK_STRENGTHS:
            weak_or_unverified_refs.append(label)
        else:
            unknown_refs.append(label)
    return {
        "total": len(refs),
        "by_strength": dict(strengths),
        "strong_refs": strong_refs,
        "weak_or_unverified_refs": weak_or_unverified_refs,
        "unknown_refs": unknown_refs,
        "has_strong_evidence": bool(strong_refs),
        "has_weak_or_unverified_evidence": bool(weak_or_unverified_refs or unknown_refs or not refs),
    }


def build_close_gate_report(
    *,
    thread_id: str,
    run_id: str,
    round_id: str | None = None,
    pending_handoffs: list[Any] | None = None,
    planned_lanes: list[Any] | None = None,
    task_lanes: list[Any] | None = None,
    review_invocations: list[Any] | None = None,
    quality_signals: list[Any] | None = None,
    chair_decisions: list[Any] | None = None,
    evidence_refs: list[Any] | None = None,
    round_state: Any | None = None,
) -> CloseGateReport:
    """Build a read-only fact report for a round close check.

    The report intentionally has no PASS/FAIL quality verdict. Strong evidence
    is only a fact signal; weak or missing evidence is only a warning/unknown.
    """

    handoffs = [_as_dict(row) for row in pending_handoffs or []]
    lanes = [_as_dict(row) for row in planned_lanes or []]
    tasks = [_as_dict(row) for row in task_lanes or []]
    reviews = [_as_dict(row) for row in review_invocations or []]
    quality = [_as_dict(row) for row in quality_signals or []]
    decisions = [_as_dict(row) for row in chair_decisions or []]
    state = _as_dict(round_state)

    scoped_handoffs = [row for row in handoffs if _matches_round(row, round_id)]
    scoped_lanes = [row for row in lanes if _matches_round(row, round_id)]
    scoped_tasks = [row for row in tasks if _matches_round(row, round_id)]
    scoped_reviews = [row for row in reviews if _matches_round(row, round_id)]
    scoped_quality = [row for row in quality if _matches_round(row, round_id)]
    scoped_decisions = [row for row in decisions if _matches_round(row, round_id)]

    open_handoffs = [row for row in scoped_handoffs if _status(row) in _OPEN_HANDOFF_STATUSES or not _status(row)]
    active_lanes = [row for row in scoped_lanes if _status(row) in _ACTIVE_LANE_STATUSES]
    active_tasks = [row for row in scoped_tasks if _status(row) in _ACTIVE_LANE_STATUSES]
    failed_blocked = [row for row in [*scoped_lanes, *scoped_tasks] if _status(row) in _FAILED_OR_BLOCKED_STATUSES]
    open_reviews = [row for row in scoped_reviews if _status(row) in _OPEN_REVIEW_STATUSES or not _status(row)]

    recommendations = Counter(str(row.get("recommendation") or "unknown").strip().lower() or "unknown" for row in scoped_quality)
    evidence_summary = _summarize_evidence(evidence_refs or [])

    facts = [
        f"open_pending_handoffs={len(open_handoffs)}",
        f"active_planned_lanes={len(active_lanes)}",
        f"active_task_lanes={len(active_tasks)}",
        f"failed_or_blocked_lanes={len(failed_blocked)}",
        f"open_review_invocations={len(open_reviews)}",
        f"quality_signals={len(scoped_quality)}",
        f"chair_decisions={len(scoped_decisions)}",
        f"evidence_refs={evidence_summary['total']}",
    ]
    warnings: list[str] = []
    unknowns: list[str] = []
    if open_handoffs:
        warnings.append("Pending handoffs remain open.")
    if active_lanes or active_tasks:
        warnings.append("Active planned/task lanes remain open.")
    if failed_blocked:
        warnings.append("Failed or blocked lanes are present.")
    if open_reviews:
        warnings.append("Requested review invocations remain open.")
    if recommendations.get("needs_more_evidence", 0):
        warnings.append("Quality signals include needs_more_evidence.")
    if evidence_summary["has_weak_or_unverified_evidence"]:
        unknowns.append("Some evidence is weak, unverified, unknown, or absent.")
    if not state:
        unknowns.append("round_state was not provided.")

    next_check_hint = "review_open_items" if warnings or unknowns else "chair_review_no_programmatic_verdict"
    lifecycle_hint = build_round_lifecycle_hint(
        round_id=round_id,
        round_state=round_state,
        pending_handoffs=scoped_handoffs,
        planned_lanes=scoped_lanes,
        task_lanes=scoped_tasks,
        review_invocations=scoped_reviews,
        chair_decisions=scoped_decisions,
    )
    lane_reconciliation = build_lane_reconciliation_facts(
        planned_lanes=scoped_lanes,
        task_lanes=scoped_tasks,
        pending_handoffs=scoped_handoffs,
    )
    return CloseGateReport(
        thread_id=thread_id,
        run_id=run_id,
        round_id=round_id,
        open_pending_handoffs=open_handoffs,
        active_planned_lanes=active_lanes,
        active_task_lanes=active_tasks,
        failed_or_blocked_lanes=failed_blocked,
        open_review_invocations=open_reviews,
        quality_recommendations=dict(recommendations),
        quality_signal_summary={"total": len(scoped_quality), "recommendations": dict(recommendations)},
        evidence_summary=evidence_summary,
        facts=facts,
        warnings=warnings,
        unknowns=unknowns,
        next_check_hint=next_check_hint,
        round_lifecycle_hint=lifecycle_hint.as_dict(),
        lane_reconciliation=lane_reconciliation.as_dict(),
    )


__all__ = ["CloseGateReport", "build_close_gate_report"]
