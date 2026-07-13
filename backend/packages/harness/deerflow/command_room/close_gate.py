"""Read-only Round Close Gate fact collection for judgment by the Chair AI."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from .lane_reconciliation import build_lane_reconciliation_facts
from .round_lifecycle import build_round_lifecycle_hint

_ACTIVE_LANE_STATUSES = {"planned", "dispatched", "running"}
_FAILED_OR_BLOCKED_STATUSES = {"failed", "blocked"}
_OPEN_REVIEW_STATUSES = {"requested"}
_OPEN_HANDOFF_STATUSES = {"pending", "open", "requested"}


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
    status_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    evidence_summary: dict[str, Any] = field(default_factory=dict)
    facts: list[str] = field(default_factory=list)
    quality_recommendations: dict[str, int] = field(default_factory=dict)
    quality_signal_summary: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    next_check_hint: None = None
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


def _counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(_status(row) or "<unset>" for row in rows))


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
    """Return scoped rows and exact counts without a verdict or next-step hint."""

    groups = {
        "pending_handoffs": pending_handoffs or [],
        "planned_lanes": planned_lanes or [],
        "task_lanes": task_lanes or [],
        "review_invocations": review_invocations or [],
        "quality_signals": quality_signals or [],
        "chair_decisions": chair_decisions or [],
    }
    scoped = {name: [row for value in values if _matches_round(row := _as_dict(value), round_id)] for name, values in groups.items()}
    handoffs = scoped["pending_handoffs"]
    lanes = scoped["planned_lanes"]
    tasks = scoped["task_lanes"]
    reviews = scoped["review_invocations"]
    open_handoffs = [row for row in handoffs if _status(row) in _OPEN_HANDOFF_STATUSES or not _status(row)]
    active_lanes = [row for row in lanes if _status(row) in _ACTIVE_LANE_STATUSES]
    active_tasks = [row for row in tasks if _status(row) in _ACTIVE_LANE_STATUSES]
    failed_blocked = [row for row in [*lanes, *tasks] if _status(row) in _FAILED_OR_BLOCKED_STATUSES]
    open_reviews = [row for row in reviews if _status(row) in _OPEN_REVIEW_STATUSES or not _status(row)]
    status_counts = {name: _counts(rows) for name, rows in scoped.items()}
    evidence_summary = {"total": len(evidence_refs or [])}
    facts = [f"round_state_present={bool(_as_dict(round_state))}", f"evidence_refs={evidence_summary['total']}"]
    facts.extend(f"{name}={len(rows)}" for name, rows in scoped.items())
    lifecycle = build_round_lifecycle_hint(
        round_id=round_id,
        round_state=round_state,
        pending_handoffs=handoffs,
        planned_lanes=lanes,
        task_lanes=tasks,
        review_invocations=reviews,
        chair_decisions=scoped["chair_decisions"],
    )
    reconciliation = build_lane_reconciliation_facts(
        planned_lanes=lanes,
        task_lanes=tasks,
        pending_handoffs=handoffs,
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
        status_counts=status_counts,
        evidence_summary=evidence_summary,
        facts=facts,
        quality_signal_summary={"total": len(scoped["quality_signals"])},
        round_lifecycle_hint=lifecycle.as_dict(),
        lane_reconciliation=reconciliation.as_dict(),
    )


__all__ = ["CloseGateReport", "build_close_gate_report"]
