"""Read-only advisory round lifecycle hint helpers.

These helpers derive mechanical lifecycle facts from already-loaded Command
Room rows. They never mutate state, dispatch work, or produce quality verdicts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

_ACTIVE_LANE_STATUSES = {"planned", "dispatched", "running"}
_OPEN_REVIEW_STATUSES = {"requested"}
_OPEN_HANDOFF_STATUSES = {"pending", "open", "requested"}
_TERMINAL_LANE_STATUSES = {"completed", "done", "succeeded", "success", "failed", "blocked", "cancelled", "canceled"}


@dataclass(frozen=True)
class RoundLifecycleHint:
    """Advisory lifecycle facts for a round.

    ``next_state_hint`` is a mechanical observation for operators only. It must
    not be written back to round_state or treated as PASS/FAIL/dispatch input.
    """

    next_state_hint: str | None
    facts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
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


def build_round_lifecycle_hint(
    *,
    round_id: str | None = None,
    round_state: Any | None = None,
    pending_handoffs: list[Any] | None = None,
    planned_lanes: list[Any] | None = None,
    task_lanes: list[Any] | None = None,
    review_invocations: list[Any] | None = None,
    chair_decisions: list[Any] | None = None,
) -> RoundLifecycleHint:
    """Build a read-only mechanical lifecycle hint from current facts."""

    state = _as_dict(round_state)
    handoffs = [row for row in (_as_dict(row) for row in pending_handoffs or []) if _matches_round(row, round_id)]
    lanes = [row for row in (_as_dict(row) for row in planned_lanes or []) if _matches_round(row, round_id)]
    tasks = [row for row in (_as_dict(row) for row in task_lanes or []) if _matches_round(row, round_id)]
    reviews = [row for row in (_as_dict(row) for row in review_invocations or []) if _matches_round(row, round_id)]
    decisions = [row for row in (_as_dict(row) for row in chair_decisions or []) if _matches_round(row, round_id)]

    open_handoffs = [row for row in handoffs if _status(row) in _OPEN_HANDOFF_STATUSES or not _status(row)]
    active_lanes = [row for row in lanes if _status(row) in _ACTIVE_LANE_STATUSES]
    active_tasks = [row for row in tasks if _status(row) in _ACTIVE_LANE_STATUSES]
    open_reviews = [row for row in reviews if _status(row) in _OPEN_REVIEW_STATUSES or not _status(row)]
    known_lanes = [*lanes, *tasks]
    terminal_lanes = [row for row in known_lanes if _status(row) in _TERMINAL_LANE_STATUSES]
    unknown_status_lanes = [row for row in known_lanes if not _status(row)]
    all_known_lanes_terminal = bool(known_lanes) and len(terminal_lanes) == len(known_lanes)

    facts = [
        f"open_pending_handoffs={len(open_handoffs)}",
        f"active_planned_lanes={len(active_lanes)}",
        f"active_task_lanes={len(active_tasks)}",
        f"open_review_invocations={len(open_reviews)}",
        f"known_lanes={len(known_lanes)}",
        f"terminal_lanes={len(terminal_lanes)}",
        f"chair_decisions={len(decisions)}",
    ]
    warnings: list[str] = []
    unknowns: list[str] = []

    if not state:
        unknowns.append("round_state was not provided.")
    if unknown_status_lanes:
        unknowns.append("Some lanes have unknown status.")

    if open_handoffs:
        next_state_hint = "waiting_user"
        warnings.append("Pending handoffs remain open; do not auto-dispatch.")
    elif active_tasks or active_lanes:
        next_state_hint = "executing"
    elif open_reviews:
        next_state_hint = "validating"
    elif all_known_lanes_terminal and not decisions:
        next_state_hint = "awaiting_chair_decision"
    elif decisions:
        next_state_hint = "chair_decision_recorded"
    else:
        next_state_hint = "insufficient_facts"
        unknowns.append("No lifecycle-driving lane, review, handoff, or chair decision facts were found.")

    return RoundLifecycleHint(
        next_state_hint=next_state_hint,
        facts=facts,
        warnings=warnings,
        unknowns=unknowns,
    )


__all__ = ["RoundLifecycleHint", "build_round_lifecycle_hint"]
