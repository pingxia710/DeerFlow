"""AI-authored review invocation records for Command Room."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from deerflow.command_room.file_records import append_jsonl_record, read_jsonl_text
from deerflow.config.paths import get_paths

ReviewerRole = Literal["evidence_checker", "opposition", "synthesis_checker", "reviewer"]
ReviewInvocationStatus = Literal["requested", "completed", "cancelled"]

REVIEWER_ROLES = frozenset({"evidence_checker", "opposition", "synthesis_checker", "reviewer"})
REVIEW_INVOCATION_STATUSES = frozenset({"requested", "completed", "cancelled"})
_TEXT_LIMIT = 2000
_REF_LIMIT = 20
_COMPACT_TEXT_LIMIT = 240


@dataclass
class ReviewInvocation:
    invocation_id: str
    thread_id: str
    run_id: str
    round_id: str | None
    task_id: str | None
    requested_by_role: str
    reviewer_role: ReviewerRole
    reason: str
    focus: str
    evidence_refs: list[str]
    handoff_refs: list[str]
    quality_signal_refs: list[str]
    status: ReviewInvocationStatus
    result_summary: str | None
    result_evidence_refs: list[str]
    target_role: str
    created_at: str
    completed_at: str | None = None
    ai_authored: bool = True
    schema_version: int = 1

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _clip(value: Any, limit: int = _TEXT_LIMIT) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _clean_optional(value: Any, limit: int = _TEXT_LIMIT) -> str | None:
    text = _clip(value, limit)
    return text or None


def _clean_list(value: Any, *, limit: int = _REF_LIMIT) -> list[str]:
    if isinstance(value, str):
        raw_items: list[Any] = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    items: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = _clip(item)
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
        if len(items) >= limit:
            break
    return items


def _reviewer_role(value: Any) -> ReviewerRole:
    role = str(value or "").strip().lower().replace("-", "_")
    if role not in REVIEWER_ROLES:
        allowed = ", ".join(sorted(REVIEWER_ROLES))
        raise ValueError(f"Unsupported reviewer_role: {role or '<empty>'}; expected one of {allowed}")
    return role  # type: ignore[return-value]


def _status(value: Any) -> ReviewInvocationStatus:
    status = str(value or "").strip().lower()
    if status not in REVIEW_INVOCATION_STATUSES:
        allowed = ", ".join(sorted(REVIEW_INVOCATION_STATUSES))
        raise ValueError(f"Unsupported review invocation status: {status or '<empty>'}; expected one of {allowed}")
    return status  # type: ignore[return-value]


def build_review_invocation(
    *,
    thread_id: str,
    run_id: str,
    requested_by_role: str,
    reviewer_role: str,
    reason: str,
    focus: str,
    round_id: str | None = None,
    task_id: str | None = None,
    evidence_refs: list[str] | None = None,
    handoff_refs: list[str] | None = None,
    quality_signal_refs: list[str] | None = None,
    status: str = "requested",
    result_summary: str | None = None,
    result_evidence_refs: list[str] | None = None,
    target_role: str = "Chair",
    invocation_id: str | None = None,
    created_at: str | None = None,
    completed_at: str | None = None,
) -> ReviewInvocation:
    requester = _clip(requested_by_role, 64).lower()
    if not requester:
        raise ValueError("Review invocation requested_by_role is required")
    cleaned_reason = _clip(reason)
    if not cleaned_reason:
        raise ValueError("Review invocation reason is required")
    cleaned_focus = _clip(focus)
    if not cleaned_focus:
        raise ValueError("Review invocation focus is required")
    cleaned_status = _status(status)
    cleaned_result = _clean_optional(result_summary)
    if cleaned_status == "completed" and not cleaned_result:
        raise ValueError("Completed review invocation requires result_summary")
    return ReviewInvocation(
        invocation_id=_clip(invocation_id, 128) or f"review-{uuid.uuid4().hex}",
        thread_id=thread_id,
        run_id=run_id,
        round_id=_clean_optional(round_id, 128),
        task_id=_clean_optional(task_id, 128),
        requested_by_role=requester,
        reviewer_role=_reviewer_role(reviewer_role),
        reason=cleaned_reason,
        focus=cleaned_focus,
        evidence_refs=_clean_list(evidence_refs),
        handoff_refs=_clean_list(handoff_refs),
        quality_signal_refs=_clean_list(quality_signal_refs),
        status=cleaned_status,
        result_summary=cleaned_result,
        result_evidence_refs=_clean_list(result_evidence_refs),
        target_role=_clip(target_role, 64) or "Chair",
        created_at=created_at or _now_iso(),
        completed_at=completed_at,
    )


def review_invocation_from_dict(data: dict[str, Any]) -> ReviewInvocation:
    return build_review_invocation(
        invocation_id=data.get("invocation_id"),
        thread_id=str(data.get("thread_id") or ""),
        run_id=str(data.get("run_id") or ""),
        round_id=data.get("round_id"),
        task_id=data.get("task_id"),
        requested_by_role=str(data.get("requested_by_role") or ""),
        reviewer_role=str(data.get("reviewer_role") or ""),
        reason=str(data.get("reason") or ""),
        focus=str(data.get("focus") or ""),
        evidence_refs=_clean_list(data.get("evidence_refs")),
        handoff_refs=_clean_list(data.get("handoff_refs")),
        quality_signal_refs=_clean_list(data.get("quality_signal_refs")),
        status=str(data.get("status") or "requested"),
        result_summary=data.get("result_summary"),
        result_evidence_refs=_clean_list(data.get("result_evidence_refs")),
        target_role=str(data.get("target_role") or "Chair"),
        created_at=str(data.get("created_at") or "") or None,
        completed_at=data.get("completed_at") if isinstance(data.get("completed_at"), str) else None,
    )


def complete_review_invocation(invocation: ReviewInvocation, *, result_summary: str, result_evidence_refs: list[str] | None = None) -> ReviewInvocation:
    summary = _clean_optional(result_summary)
    if not summary:
        raise ValueError("Completed review invocation requires result_summary")
    return replace(
        invocation,
        status="completed",
        result_summary=summary,
        result_evidence_refs=_clean_list(result_evidence_refs),
        completed_at=_now_iso(),
    )


def _review_invocations_file(thread_id: str, user_id: str | None, base_dir: Path | None = None) -> Path:
    if base_dir is not None:
        return base_dir / "review_invocations.jsonl"
    return get_paths().thread_dir(thread_id, user_id=user_id) / "audit" / "review_invocations.jsonl"


def record_review_invocation(invocation: ReviewInvocation, *, user_id: str | None = None, base_dir: Path | None = None) -> Path:
    path = _review_invocations_file(invocation.thread_id, user_id, base_dir=base_dir)
    return append_jsonl_record(path, invocation.as_dict())


def list_review_invocations(
    *,
    thread_id: str,
    user_id: str | None,
    run_id: str | None = None,
    round_id: str | None = None,
    task_id: str | None = None,
    limit: int = 50,
    base_dir: Path | None = None,
) -> list[dict[str, Any]]:
    path = _review_invocations_file(thread_id, user_id, base_dir=base_dir)
    text = read_jsonl_text(path)
    if text is None:
        return []
    latest: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("thread_id") != thread_id:
            continue
        if run_id is not None and row.get("run_id") != run_id:
            continue
        if round_id is not None and row.get("round_id") != round_id:
            continue
        if task_id is not None and row.get("task_id") != task_id:
            continue
        invocation_id = str(row.get("invocation_id") or "")
        if not invocation_id:
            continue
        if invocation_id not in latest:
            order.append(invocation_id)
        latest[invocation_id] = row
    return [latest[item] for item in order][-limit:]


def compact_review_invocations(invocations: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for invocation in invocations[-limit:]:
        compact.append(
            {
                "invocation_id": invocation.get("invocation_id"),
                "run_id": invocation.get("run_id"),
                "round_id": invocation.get("round_id"),
                "task_id": invocation.get("task_id"),
                "requested_by_role": invocation.get("requested_by_role"),
                "reviewer_role": invocation.get("reviewer_role"),
                "status": invocation.get("status"),
                "focus": _clip(invocation.get("focus"), _COMPACT_TEXT_LIMIT),
                "target_role": invocation.get("target_role") or "Chair",
                "result_summary": _clip(invocation.get("result_summary"), _COMPACT_TEXT_LIMIT),
            }
        )
    return compact


__all__ = [
    "REVIEWER_ROLES",
    "REVIEW_INVOCATION_STATUSES",
    "ReviewInvocation",
    "ReviewerRole",
    "ReviewInvocationStatus",
    "build_review_invocation",
    "compact_review_invocations",
    "complete_review_invocation",
    "list_review_invocations",
    "record_review_invocation",
    "review_invocation_from_dict",
]
