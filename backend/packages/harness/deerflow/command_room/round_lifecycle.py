"""Factual round lifecycle counts for the Chair AI."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class RoundLifecycleHint:
    """Compatibility shape with no program-selected next state."""

    next_state_hint: None = None
    facts: list[str] = field(default_factory=list)
    status_counts: dict[str, dict[str, int]] = field(default_factory=dict)
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


def _status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(row.get("status") or "<unset>") for row in rows))


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
    """Count scoped rows and statuses; the Chair AI chooses the next state."""

    groups = {
        "pending_handoffs": pending_handoffs or [],
        "planned_lanes": planned_lanes or [],
        "task_lanes": task_lanes or [],
        "review_invocations": review_invocations or [],
        "chair_decisions": chair_decisions or [],
    }
    scoped = {name: [row for value in values if _matches_round(row := _as_dict(value), round_id)] for name, values in groups.items()}
    facts = [f"round_state_present={bool(_as_dict(round_state))}"]
    facts.extend(f"{name}={len(rows)}" for name, rows in scoped.items())
    return RoundLifecycleHint(
        facts=facts,
        status_counts={name: _status_counts(rows) for name, rows in scoped.items()},
    )


__all__ = ["RoundLifecycleHint", "build_round_lifecycle_hint"]
