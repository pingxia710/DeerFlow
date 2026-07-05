"""Chair Operating Brief read-model helpers.

The brief is a compact context packet for Chair/lead AI. It aggregates facts
from existing records and deliberately avoids program-side quality judgment.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from deerflow.command_room.quality import compact_quality_signals
from deerflow.command_room.review import compact_review_invocations

_TEXT_LIMIT = 240
_REF_LIMIT = 3
_COMPACT_LIMIT = 3
_KNOWN_GAPS = frozenset({"no_capability_snapshot", "no_evidence_refs", "no_quality_signals"})
_CHAIR_BRIEF_HEADER = "[Internal Chair Operating Brief]"


@dataclass(frozen=True)
class ChairOperatingBrief:
    thread_id: str
    run_id: str
    round_id: str | None
    task_id: str | None
    generated_at: str
    capability_snapshot_version: int | None
    handoff_count: int
    latest_handoff: dict[str, Any] | None
    evidence_summary: dict[str, Any]
    quality_signals: list[dict[str, Any]]
    review_invocations: list[dict[str, Any]]
    account_proposals: list[dict[str, Any]]
    account_decisions: list[dict[str, Any]]
    known_gaps: list[str]
    source_counts: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _clip(value: Any, limit: int = _TEXT_LIMIT) -> str:
    return str(value or "").strip()[:limit]


def _clean_list(value: Any, *, limit: int = _REF_LIMIT) -> list[str]:
    if isinstance(value, str):
        raw_items: list[Any] = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = _clip(item)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _row_matches(row: Mapping[str, Any], *, round_id: str | None, task_id: str | None) -> bool:
    if round_id is not None and row.get("round_id") != round_id:
        return False
    if task_id is not None and row.get("task_id") != task_id:
        return False
    return True


def _filter_rows(rows: list[dict[str, Any]], *, round_id: str | None, task_id: str | None) -> list[dict[str, Any]]:
    if round_id is None and task_id is None:
        return rows
    return [row for row in rows if _row_matches(row, round_id=round_id, task_id=task_id)]


def _compact_handoff(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    handoff = row.get("handoff") if isinstance(row.get("handoff"), Mapping) else row
    if not isinstance(handoff, Mapping):
        return None
    return {
        "task_id": row.get("task_id"),
        "role": row.get("role"),
        "status": row.get("status"),
        "source_role": handoff.get("sourceRole") or handoff.get("source_role"),
        "target_role": handoff.get("targetRole") or handoff.get("target_role"),
        "task_or_question": _clip(handoff.get("taskOrQuestion") or handoff.get("task_or_question")),
        "expected_output": _clip(handoff.get("expectedOutput") or handoff.get("expected_output")),
        "evidence_strength": handoff.get("evidenceStrength") or handoff.get("evidence_strength"),
        "evidence_refs": _clean_list(handoff.get("evidenceRefs") or handoff.get("evidence_refs")),
        "handoff_file": handoff.get("handoffFile") or handoff.get("handoff_file"),
        "artifact_refs": _clean_list(handoff.get("artifactRefs") or handoff.get("artifact_refs")),
        "recommended_next_decision": _clip(handoff.get("recommendedNextDecision") or handoff.get("recommended_next_decision")),
    }


def _evidence_refs(evidence: Mapping[str, Any] | None, *, round_id: str | None, task_id: str | None) -> list[dict[str, Any]]:
    if not evidence:
        return []
    refs = evidence.get("evidence_refs")
    if not isinstance(refs, list):
        return []
    rows = [dict(ref) if isinstance(ref, Mapping) else {"ref": _clip(ref)} for ref in refs]
    return _filter_rows(rows, round_id=round_id, task_id=task_id)


def _evidence_summary(refs: list[dict[str, Any]]) -> dict[str, Any]:
    by_source_kind: dict[str, int] = {}
    by_strength = {"Strong": 0, "Weak": 0, "Unverified": 0}
    recent_refs: list[dict[str, Any]] = []
    for ref in refs:
        source_kind = str(ref.get("source_kind") or "unknown")
        strength = str(ref.get("strength") or "Unverified")
        by_source_kind[source_kind] = by_source_kind.get(source_kind, 0) + 1
        if strength in by_strength:
            by_strength[strength] += 1
        if len(recent_refs) < _REF_LIMIT:
            recent_refs.append(
                {
                    "ref_id": ref.get("ref_id"),
                    "source_kind": source_kind,
                    "strength": strength,
                    "ref": _clip(ref.get("ref")),
                }
            )
    return {
        "total": len(refs),
        "by_source_kind": by_source_kind,
        "by_strength": by_strength,
        "recent_refs": recent_refs,
    }


def _compact_quality(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    compact = compact_quality_signals(rows, limit=limit)
    for row in compact:
        row.pop("quality_verdict", None)
        row.pop("auto_rework", None)
    return compact


def _compact_account_proposals(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    return [
        {
            "proposal_id": row.get("proposal_id"),
            "run_id": row.get("run_id"),
            "round_id": row.get("round_id"),
            "task_id": row.get("task_id"),
            "proposed_by_role": row.get("proposed_by_role"),
            "account_type": row.get("account_type"),
            "target_role": row.get("target_role") or "Chair",
            "evidence_refs": _clean_list(row.get("evidence_refs")),
            "created_at": row.get("created_at"),
        }
        for row in rows[-limit:]
    ]


def _matching_account_decisions(rows: list[dict[str, Any]], *, proposals: list[dict[str, Any]], scoped: bool) -> list[dict[str, Any]]:
    proposal_ids = {str(row.get("proposal_id")) for row in proposals if row.get("proposal_id")}
    if scoped:
        return [row for row in rows if str(row.get("proposal_id")) in proposal_ids]
    return rows


def _compact_account_decisions(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    return [
        {
            "decision_id": row.get("decision_id"),
            "proposal_id": row.get("proposal_id"),
            "run_id": row.get("run_id"),
            "decided_by_role": row.get("decided_by_role") or "chair",
            "decision": row.get("decision"),
            "evidence_refs": _clean_list(row.get("evidence_refs")),
            "created_at": row.get("created_at"),
        }
        for row in rows[-limit:]
    ]


def _known_gaps(*, capability_snapshot_version: int | None, evidence_count: int, quality_count: int) -> list[str]:
    gaps: list[str] = []
    if capability_snapshot_version is None:
        gaps.append("no_capability_snapshot")
    if evidence_count == 0:
        gaps.append("no_evidence_refs")
    if quality_count == 0:
        gaps.append("no_quality_signals")
    return [gap for gap in gaps if gap in _KNOWN_GAPS]


def build_chair_operating_brief(
    *,
    thread_id: str,
    run_id: str,
    round_id: str | None = None,
    task_id: str | None = None,
    filter_round_id: str | None = None,
    capability_snapshot: Mapping[str, Any] | None = None,
    handoffs: list[dict[str, Any]] | None = None,
    evidence: Mapping[str, Any] | None = None,
    quality_signals: list[dict[str, Any]] | None = None,
    review_invocations: list[dict[str, Any]] | None = None,
    account_proposals: list[dict[str, Any]] | None = None,
    account_decisions: list[dict[str, Any]] | None = None,
    generated_at: str | None = None,
    compact_limit: int = _COMPACT_LIMIT,
) -> ChairOperatingBrief:
    """Build a compact read model from existing Command Room facts."""

    handoff_rows = _filter_rows(list(handoffs or []), round_id=filter_round_id, task_id=task_id)
    evidence_rows = _evidence_refs(evidence, round_id=filter_round_id, task_id=task_id)
    quality_rows = _filter_rows(list(quality_signals or []), round_id=filter_round_id, task_id=task_id)
    review_rows = _filter_rows(list(review_invocations or []), round_id=filter_round_id, task_id=task_id)
    proposal_rows = _filter_rows(list(account_proposals or []), round_id=filter_round_id, task_id=task_id)
    decision_rows = _matching_account_decisions(list(account_decisions or []), proposals=proposal_rows, scoped=filter_round_id is not None or task_id is not None)
    snapshot_version = capability_snapshot.get("version") if isinstance(capability_snapshot, Mapping) and isinstance(capability_snapshot.get("version"), int) else None
    evidence_summary = _evidence_summary(evidence_rows)

    return ChairOperatingBrief(
        thread_id=thread_id,
        run_id=run_id,
        round_id=round_id,
        task_id=task_id,
        generated_at=generated_at or _now_iso(),
        capability_snapshot_version=snapshot_version,
        handoff_count=len(handoff_rows),
        latest_handoff=_compact_handoff(handoff_rows[-1] if handoff_rows else None),
        evidence_summary=evidence_summary,
        quality_signals=_compact_quality(quality_rows, limit=compact_limit),
        review_invocations=compact_review_invocations(review_rows, limit=compact_limit),
        account_proposals=_compact_account_proposals(proposal_rows, limit=compact_limit),
        account_decisions=_compact_account_decisions(decision_rows, limit=compact_limit),
        known_gaps=_known_gaps(capability_snapshot_version=snapshot_version, evidence_count=len(evidence_rows), quality_count=len(quality_rows)),
        source_counts={
            "handoffs": len(handoff_rows),
            "evidence_refs": len(evidence_rows),
            "quality_signals": len(quality_rows),
            "review_invocations": len(review_rows),
            "account_proposals": len(proposal_rows),
            "account_decisions": len(decision_rows),
        },
    )


def format_chair_operating_brief_for_model(brief: ChairOperatingBrief | Mapping[str, Any] | None) -> str | None:
    if brief is None:
        return None
    data = brief.as_dict() if isinstance(brief, ChairOperatingBrief) else dict(brief)
    lines = [
        _CHAIR_BRIEF_HEADER,
        "Facts only. Chair decides next step; program does not judge quality or dispatch reviewers.",
        f"thread_id={data.get('thread_id')}; run_id={data.get('run_id')}; round_id={data.get('round_id')}; task_id={data.get('task_id')}",
    ]
    if data.get("capability_snapshot_version") is not None:
        lines.append(f"capability_snapshot_version={data.get('capability_snapshot_version')}")
    counts = data.get("source_counts") if isinstance(data.get("source_counts"), Mapping) else {}
    if counts:
        lines.append("source_counts: " + "; ".join(f"{key}={value}" for key, value in counts.items()))
    gaps = _clean_list(data.get("known_gaps"), limit=6)
    if gaps:
        lines.append("known_gaps: " + "; ".join(gaps))
    latest_handoff = data.get("latest_handoff") if isinstance(data.get("latest_handoff"), Mapping) else {}
    if latest_handoff:
        lines.append(
            "latest_handoff: "
            + "; ".join(
                item
                for item in (
                    f"target={latest_handoff.get('target_role')}",
                    f"task={latest_handoff.get('task_or_question')}",
                    f"evidence_strength={latest_handoff.get('evidence_strength')}",
                )
                if not item.endswith("=None") and not item.endswith("=")
            )
        )
    evidence = data.get("evidence_summary") if isinstance(data.get("evidence_summary"), Mapping) else {}
    if evidence:
        lines.append(f"evidence: total={evidence.get('total', 0)}; by_strength={evidence.get('by_strength', {})}")
    for signal in data.get("quality_signals") or []:
        if isinstance(signal, Mapping):
            lines.append(f"quality_signal: author={signal.get('author_role')}; recommendation={signal.get('recommendation')}; target={signal.get('target_role') or 'Chair'}")
    for invocation in data.get("review_invocations") or []:
        if isinstance(invocation, Mapping):
            lines.append(f"review_invocation: reviewer={invocation.get('reviewer_role')}; status={invocation.get('status')}; target={invocation.get('target_role') or 'Chair'}")
    for proposal in data.get("account_proposals") or []:
        if isinstance(proposal, Mapping):
            lines.append(f"account_proposal: account_type={proposal.get('account_type')}; proposed_by_role={proposal.get('proposed_by_role')}; target={proposal.get('target_role') or 'Chair'}")
    for decision in data.get("account_decisions") or []:
        if isinstance(decision, Mapping):
            lines.append(f"account_decision: decision={decision.get('decision')}; proposal_id={decision.get('proposal_id')}")
    return "\n".join(lines)


__all__ = [
    "ChairOperatingBrief",
    "build_chair_operating_brief",
    "format_chair_operating_brief_for_model",
]
