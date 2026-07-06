"""Chair-accepted native planning objects for Command Room."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from deerflow.config.paths import get_paths

LaneStatus = Literal["planned", "dispatched", "running", "completed", "failed", "skipped", "blocked", "superseded"]
LANE_STATUSES = frozenset({"planned", "dispatched", "running", "completed", "failed", "skipped", "blocked", "superseded"})
DecisionStatus = Literal["recorded", "resolved", "superseded"]
_TEXT_LIMIT = 2000
_COMPACT_TEXT_LIMIT = 240
_REF_LIMIT = 20
_WRITE_LOCK = threading.Lock()


@dataclass
class PlannedLane:
    lane_id: str
    thread_id: str
    run_id: str
    round_id: str | None
    target_role: str
    reason: str
    expected_evidence: str | None
    status: LaneStatus
    linked_task_id: str | None
    evidence_refs: list[str]
    artifact_refs: list[str]
    output_refs: list[str]
    created_at: str
    updated_at: str
    dispatched_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    ai_authored: bool = True
    programmatic_decision: bool = False
    auto_dispatch: bool = False
    schema_version: int = 1

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RoundPlan:
    plan_id: str
    thread_id: str
    run_id: str
    round_id: str | None
    goal: str
    boundary: str | None
    evidence_standard: str | None
    capability_release: list[str]
    status: str
    dispatch_plan: str | None
    planned_lanes: list[dict[str, Any]]
    created_by_role: str
    created_at: str
    updated_at: str
    ai_authored: bool = True
    programmatic_decision: bool = False
    auto_dispatch: bool = False
    schema_version: int = 1

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChairDecision:
    decision_id: str
    thread_id: str
    run_id: str
    round_id: str | None
    decision_type: str
    status: DecisionStatus
    decision: str
    reason: str
    evidence_refs: list[str]
    affected_lanes: list[str]
    handoffs: list[str]
    created_by_role: str
    created_at: str
    ai_authored: bool = True
    programmatic_decision: bool = False
    auto_dispatch: bool = False
    schema_version: int = 1

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _clip(value: Any, limit: int = _TEXT_LIMIT) -> str:
    return str(value or "").strip()[:limit]


def _clean_optional(value: Any, limit: int = _TEXT_LIMIT) -> str | None:
    text = _clip(value, limit)
    return text or None


def _clean_list(value: Any, *, limit: int = _REF_LIMIT) -> list[str]:
    raw = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = _clip(item)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _lane_status(value: Any) -> LaneStatus:
    status = str(value or "").strip().lower()
    if status not in LANE_STATUSES:
        raise ValueError(f"Unsupported planned lane status: {status or '<empty>'}; expected one of {', '.join(sorted(LANE_STATUSES))}")
    return status  # type: ignore[return-value]


def build_planned_lane(
    *,
    thread_id: str,
    run_id: str,
    target_role: str,
    reason: str,
    round_id: str | None = None,
    expected_evidence: str | None = None,
    status: str = "planned",
    linked_task_id: str | None = None,
    evidence_refs: list[str] | None = None,
    artifact_refs: list[str] | None = None,
    output_refs: list[str] | None = None,
    lane_id: str | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> PlannedLane:
    role = _clip(target_role, 64)
    why = _clip(reason)
    if not role:
        raise ValueError("Planned lane target_role is required")
    if not why:
        raise ValueError("Planned lane reason is required")
    now = _now_iso()
    return PlannedLane(
        _clip(lane_id, 128) or f"lane-{uuid.uuid4().hex}",
        thread_id,
        run_id,
        _clean_optional(round_id, 128),
        role,
        why,
        _clean_optional(expected_evidence),
        _lane_status(status),
        _clean_optional(linked_task_id, 128),
        _clean_list(evidence_refs),
        _clean_list(artifact_refs),
        _clean_list(output_refs),
        created_at or now,
        updated_at or now,
    )


def build_round_plan(
    *,
    thread_id: str,
    run_id: str,
    goal: str,
    round_id: str | None = None,
    boundary: str | None = None,
    evidence_standard: str | None = None,
    capability_release: list[str] | None = None,
    status: str = "planned",
    dispatch_plan: str | None = None,
    planned_lanes: list[dict[str, Any]] | None = None,
    created_by_role: str = "chair",
    plan_id: str | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> RoundPlan:
    cleaned_goal = _clip(goal)
    if not cleaned_goal:
        raise ValueError("Round plan goal is required")
    now = _now_iso()
    return RoundPlan(
        _clip(plan_id, 128) or f"round-plan-{uuid.uuid4().hex}",
        thread_id,
        run_id,
        _clean_optional(round_id, 128),
        cleaned_goal,
        _clean_optional(boundary),
        _clean_optional(evidence_standard),
        _clean_list(capability_release),
        _clip(status, 64) or "planned",
        _clean_optional(dispatch_plan),
        planned_lanes or [],
        _clip(created_by_role, 64).lower() or "chair",
        created_at or now,
        updated_at or now,
    )


def build_chair_decision(
    *,
    thread_id: str,
    run_id: str,
    decision_type: str,
    decision: str,
    reason: str,
    round_id: str | None = None,
    status: str = "recorded",
    evidence_refs: list[str] | None = None,
    affected_lanes: list[str] | None = None,
    handoffs: list[str] | None = None,
    created_by_role: str = "chair",
    decision_id: str | None = None,
    created_at: str | None = None,
) -> ChairDecision:
    dtype, dec, why = _clip(decision_type, 64), _clip(decision), _clip(reason)
    if not dtype:
        raise ValueError("Chair decision decision_type is required")
    if not dec:
        raise ValueError("Chair decision decision is required")
    if not why:
        raise ValueError("Chair decision reason is required")
    st = str(status or "recorded").strip().lower()
    if st not in {"recorded", "resolved", "superseded"}:
        raise ValueError("Unsupported chair decision status")
    return ChairDecision(
        _clip(decision_id, 128) or f"chair-decision-{uuid.uuid4().hex}",
        thread_id,
        run_id,
        _clean_optional(round_id, 128),
        dtype,
        st,
        dec,
        why,
        _clean_list(evidence_refs),
        _clean_list(affected_lanes),
        _clean_list(handoffs),
        _clip(created_by_role, 64).lower() or "chair",
        created_at or _now_iso(),
    )  # type: ignore[arg-type]


def _file(thread_id: str, user_id: str | None, name: str, base_dir: Path | None = None) -> Path:
    if base_dir is not None:
        return base_dir / name
    return get_paths().thread_dir(thread_id, user_id=user_id) / "audit" / name


def _append(path: Path, row: dict[str, Any]) -> Path:
    with _WRITE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def record_round_plan(plan: RoundPlan, *, user_id: str | None = None, base_dir: Path | None = None) -> Path:
    return _append(_file(plan.thread_id, user_id, "round_plans.jsonl", base_dir), plan.as_dict())


def record_planned_lane(lane: PlannedLane, *, user_id: str | None = None, base_dir: Path | None = None) -> Path:
    return _append(_file(lane.thread_id, user_id, "planned_lanes.jsonl", base_dir), lane.as_dict())


def record_chair_decision(decision: ChairDecision, *, user_id: str | None = None, base_dir: Path | None = None) -> Path:
    return _append(_file(decision.thread_id, user_id, "chair_decisions.jsonl", base_dir), decision.as_dict())


def _read_latest(path: Path, thread_id: str, key: str, run_id: str | None = None, round_id: str | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    latest: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("thread_id") != thread_id or (run_id is not None and row.get("run_id") != run_id) or (round_id is not None and row.get("round_id") != round_id):
            continue
        rid = str(row.get(key) or "")
        if not rid:
            continue
        if rid not in latest:
            order.append(rid)
        latest[rid] = row
    return [latest[i] for i in order]


def list_round_plans(*, thread_id: str, user_id: str | None, run_id: str | None = None, round_id: str | None = None, limit: int = 50, base_dir: Path | None = None) -> list[dict[str, Any]]:
    return _read_latest(_file(thread_id, user_id, "round_plans.jsonl", base_dir), thread_id, "plan_id", run_id, round_id)[-limit:]


def latest_round_plan(**kwargs: Any) -> dict[str, Any] | None:
    rows = list_round_plans(**kwargs, limit=1)
    return rows[-1] if rows else None


def list_planned_lanes(*, thread_id: str, user_id: str | None, run_id: str | None = None, round_id: str | None = None, status: str | None = None, limit: int = 100, base_dir: Path | None = None) -> list[dict[str, Any]]:
    rows = _read_latest(_file(thread_id, user_id, "planned_lanes.jsonl", base_dir), thread_id, "lane_id", run_id, round_id)
    if status is not None:
        rows = [r for r in rows if r.get("status") == _lane_status(status)]
    return rows[-limit:]


def list_chair_decisions(*, thread_id: str, user_id: str | None, run_id: str | None = None, round_id: str | None = None, limit: int = 100, base_dir: Path | None = None) -> list[dict[str, Any]]:
    return _read_latest(_file(thread_id, user_id, "chair_decisions.jsonl", base_dir), thread_id, "decision_id", run_id, round_id)[-limit:]


def update_planned_lane_status(
    *,
    thread_id: str,
    user_id: str | None,
    lane_id: str,
    status: str,
    run_id: str | None = None,
    linked_task_id: str | None = None,
    evidence_refs: list[str] | None = None,
    artifact_refs: list[str] | None = None,
    output_refs: list[str] | None = None,
    base_dir: Path | None = None,
) -> PlannedLane | None:
    rows = list_planned_lanes(thread_id=thread_id, user_id=user_id, run_id=run_id, status=None, limit=1000, base_dir=base_dir)
    row = next((r for r in rows if r.get("lane_id") == lane_id), None)
    if row is None:
        return None
    lane = PlannedLane(
        **{
            **row,
            "status": _lane_status(status),
            "updated_at": _now_iso(),
            "linked_task_id": _clean_optional(linked_task_id, 128) or row.get("linked_task_id"),
            "evidence_refs": _clean_list((row.get("evidence_refs") or []) + (evidence_refs or [])),
            "artifact_refs": _clean_list((row.get("artifact_refs") or []) + (artifact_refs or [])),
            "output_refs": _clean_list((row.get("output_refs") or []) + (output_refs or [])),
        }
    )
    record_planned_lane(lane, user_id=user_id, base_dir=base_dir)
    return lane


def compact_round_plans(plans: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
    return [
        {
            "plan_id": p.get("plan_id"),
            "round_id": p.get("round_id"),
            "goal": _clip(p.get("goal"), _COMPACT_TEXT_LIMIT),
            "status": p.get("status"),
            "planned_lanes": p.get("planned_lanes", [])[:5],
            "ai_authored": True,
            "programmatic_decision": False,
            "auto_dispatch": False,
        }
        for p in plans[-limit:]
    ]


def compact_planned_lanes(lanes: list[dict[str, Any]], *, limit: int = 10) -> list[dict[str, Any]]:
    return [
        {
            "lane_id": lane.get("lane_id"),
            "target_role": lane.get("target_role"),
            "reason": _clip(lane.get("reason"), _COMPACT_TEXT_LIMIT),
            "status": lane.get("status"),
            "linked_task_id": lane.get("linked_task_id"),
            "ai_authored": True,
            "programmatic_decision": False,
            "auto_dispatch": False,
        }
        for lane in lanes[-limit:]
    ]


def compact_chair_decisions(decisions: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    return [
        {
            "decision_id": d.get("decision_id"),
            "decision_type": d.get("decision_type"),
            "decision": _clip(d.get("decision"), _COMPACT_TEXT_LIMIT),
            "reason": _clip(d.get("reason"), _COMPACT_TEXT_LIMIT),
            "ai_authored": True,
            "programmatic_decision": False,
            "auto_dispatch": False,
        }
        for d in decisions[-limit:]
    ]


__all__ = [
    "LANE_STATUSES",
    "LaneStatus",
    "RoundPlan",
    "PlannedLane",
    "ChairDecision",
    "build_round_plan",
    "build_planned_lane",
    "build_chair_decision",
    "record_round_plan",
    "record_planned_lane",
    "record_chair_decision",
    "list_round_plans",
    "latest_round_plan",
    "list_planned_lanes",
    "list_chair_decisions",
    "update_planned_lane_status",
    "compact_round_plans",
    "compact_planned_lanes",
    "compact_chair_decisions",
]
