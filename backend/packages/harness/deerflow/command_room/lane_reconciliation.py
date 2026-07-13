"""Read-only lane reconciliation diagnostics for close gate reports."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class LaneReconciliationFacts:
    facts: list[dict[str, Any]] = field(default_factory=list)
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


def _text(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _role(row: dict[str, Any]) -> str | None:
    value = _text(row, "target_role", "role", "subagent_type")
    return value.lower() if value else None


def build_lane_reconciliation_facts(
    *,
    planned_lanes: list[Any] | None = None,
    task_lanes: list[Any] | None = None,
    pending_handoffs: list[Any] | None = None,
) -> LaneReconciliationFacts:
    """Build advisory facts about planned/task/handoff lane mismatches.

    This function is intentionally read-only: it does not update lanes, resolve
    handoffs, dispatch work, or produce a PASS/FAIL verdict.
    """

    planned = [_as_dict(row) for row in planned_lanes or []]
    tasks = [_as_dict(row) for row in task_lanes or []]
    handoffs = [_as_dict(row) for row in pending_handoffs or []]

    facts: list[dict[str, Any]] = []
    tasks_by_id = {task_id: row for row in tasks if (task_id := _text(row, "task_id", "id"))}
    linked_task_ids: set[str] = set()
    planned_by_lane_id = {lane_id: row for row in planned if (lane_id := _text(row, "lane_id", "id"))}

    for lane in planned:
        lane_id = _text(lane, "lane_id", "id")
        linked_task_id = _text(lane, "linked_task_id", "task_id")
        if not linked_task_id:
            continue
        linked_task_ids.add(linked_task_id)
        task = tasks_by_id.get(linked_task_id)
        if task is None:
            facts.append({"type": "planned_lane_linked_task_missing", "lane_id": lane_id, "linked_task_id": linked_task_id})
            continue
        lane_role = _role(lane)
        task_role = _role(task)
        if lane_role and task_role and lane_role != task_role:
            facts.append(
                {
                    "type": "planned_task_role_mismatch",
                    "lane_id": lane_id,
                    "linked_task_id": linked_task_id,
                    "planned_role": lane_role,
                    "task_role": task_role,
                }
            )
        elif not lane_role or not task_role:
            facts.append(
                {
                    "type": "planned_task_role_not_comparable",
                    "lane_id": lane_id,
                    "linked_task_id": linked_task_id,
                    "planned_role": lane_role,
                    "task_role": task_role,
                }
            )

    for task in tasks:
        task_id = _text(task, "task_id", "id")
        if task_id and task_id not in linked_task_ids:
            facts.append({"type": "task_lane_without_linked_planned_lane", "task_id": task_id, "task_role": _role(task)})

    for handoff in handoffs:
        handoff_id = _text(handoff, "handoff_id", "id")
        target_role = _role(handoff)
        lane_id = _text(handoff, "lane_id", "planned_lane_id", "linked_lane_id")
        if not lane_id:
            if target_role:
                facts.append(
                    {
                        "type": "pending_handoff_without_planned_lane_link",
                        "handoff_id": handoff_id,
                        "target_role": target_role,
                    }
                )
            continue
        lane = planned_by_lane_id.get(lane_id)
        if lane is None:
            facts.append(
                {
                    "type": "pending_handoff_planned_lane_missing",
                    "handoff_id": handoff_id,
                    "lane_id": lane_id,
                }
            )
            continue
        lane_role = _role(lane)
        if target_role and lane_role and target_role != lane_role:
            facts.append(
                {
                    "type": "pending_handoff_planned_lane_role_mismatch",
                    "handoff_id": handoff_id,
                    "lane_id": lane_id,
                    "handoff_target_role": target_role,
                    "planned_role": lane_role,
                }
            )
        elif not target_role or not lane_role:
            facts.append(
                {
                    "type": "pending_handoff_planned_lane_role_not_comparable",
                    "handoff_id": handoff_id,
                    "lane_id": lane_id,
                    "handoff_target_role": target_role,
                    "planned_role": lane_role,
                }
            )

    return LaneReconciliationFacts(facts=facts)


__all__ = ["LaneReconciliationFacts", "build_lane_reconciliation_facts"]
