"""Pending AI-to-AI handoff suggestions for Command Room."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from deerflow.command_room.file_records import append_jsonl_record, read_jsonl_text
from deerflow.command_room.handoff import HandoffEnvelope, handoff_envelope_to_audit_dict
from deerflow.config.paths import get_paths

PendingHandoffStatus = Literal["pending", "accepted", "dismissed", "superseded"]

PENDING_HANDOFF_STATUSES = frozenset({"pending", "accepted", "dismissed", "superseded"})
_TEXT_LIMIT = 2000
_COMPACT_TEXT_LIMIT = 240
_REF_LIMIT = 20


@dataclass
class PendingHandoff:
    handoff_id: str
    thread_id: str
    run_id: str
    round_id: str | None
    task_id: str | None
    source_role: str
    target_role: str
    task_or_question: str
    status: PendingHandoffStatus
    handoff: dict[str, Any]
    evidence_strength: str
    evidence_refs: list[str]
    artifact_refs: list[str]
    output_refs: list[str]
    created_at: str
    updated_at: str
    resolved_by_role: str | None = None
    resolution_note: str | None = None
    ai_authored: bool = True
    programmatic_dispatch: bool = False
    auto_dispatch: bool = False
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


def _status(value: Any) -> PendingHandoffStatus:
    status = str(value or "").strip().lower()
    if status not in PENDING_HANDOFF_STATUSES:
        allowed = ", ".join(sorted(PENDING_HANDOFF_STATUSES))
        raise ValueError(f"Unsupported pending handoff status: {status or '<empty>'}; expected one of {allowed}")
    return status  # type: ignore[return-value]


def _handoff_id(*, thread_id: str, run_id: str, task_id: str | None, envelope: HandoffEnvelope) -> str:
    source = "|".join(
        [
            thread_id,
            run_id,
            task_id or "",
            envelope.source_role,
            envelope.target_role,
            envelope.task_or_question,
            envelope.raw_input_sha256 or "",
        ]
    )
    return f"handoff-{hashlib.sha256(source.encode('utf-8')).hexdigest()[:24]}"


def build_pending_handoff(
    *,
    thread_id: str,
    run_id: str,
    envelope: HandoffEnvelope,
    round_id: str | None = None,
    task_id: str | None = None,
    status: str = "pending",
    handoff_id: str | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
    resolved_by_role: str | None = None,
    resolution_note: str | None = None,
) -> PendingHandoff:
    target_role = _clip(envelope.target_role, 64)
    if not target_role:
        raise ValueError("Pending handoff target_role is required")
    source_role = _clip(envelope.source_role, 64).lower() or "command-room"
    task_or_question = _clip(envelope.task_or_question) or "AI-to-AI handoff"
    now = _now_iso()
    return PendingHandoff(
        handoff_id=_clip(handoff_id, 128) or _handoff_id(thread_id=thread_id, run_id=run_id, task_id=task_id, envelope=envelope),
        thread_id=thread_id,
        run_id=run_id,
        round_id=_clean_optional(round_id, 128),
        task_id=_clean_optional(task_id, 128),
        source_role=source_role,
        target_role=target_role,
        task_or_question=task_or_question,
        status=_status(status),
        handoff=handoff_envelope_to_audit_dict(envelope),
        evidence_strength=_clip(envelope.evidence_strength, 32) or "Unverified",
        evidence_refs=_clean_list(envelope.evidence_refs),
        artifact_refs=_clean_list(envelope.artifact_refs),
        output_refs=_clean_list(envelope.output_refs),
        created_at=created_at or now,
        updated_at=updated_at or now,
        resolved_by_role=_clean_optional(resolved_by_role, 64),
        resolution_note=_clean_optional(resolution_note),
    )


def pending_handoff_from_dict(data: dict[str, Any]) -> PendingHandoff:
    handoff_data = data.get("handoff") if isinstance(data.get("handoff"), dict) else {}
    envelope = HandoffEnvelope(
        source_role=str(data.get("source_role") or handoff_data.get("sourceRole") or ""),
        target_role=str(data.get("target_role") or handoff_data.get("targetRole") or ""),
        task_or_question=str(data.get("task_or_question") or handoff_data.get("taskOrQuestion") or handoff_data.get("goal") or ""),
        evidence_refs=_clean_list(data.get("evidence_refs") or handoff_data.get("evidenceRefs")),
        evidence_strength=str(data.get("evidence_strength") or handoff_data.get("evidenceStrength") or "Unverified"),
        output_refs=_clean_list(data.get("output_refs") or handoff_data.get("outputRefs")),
        handoff_file=handoff_data.get("handoffFile") if isinstance(handoff_data.get("handoffFile"), str) else None,
        artifact_refs=_clean_list(data.get("artifact_refs") or handoff_data.get("artifactRefs")),
        released_capabilities=_clean_list(handoff_data.get("releasedCapabilities")),
        stop_conditions=_clean_list(handoff_data.get("stopConditions")),
        recommended_next_decision=str(handoff_data.get("recommendedNextDecision") or ""),
        raw_input_ref=handoff_data.get("rawInputRef") if isinstance(handoff_data.get("rawInputRef"), str) else None,
        raw_input_sha256=handoff_data.get("rawInputSha256") if isinstance(handoff_data.get("rawInputSha256"), str) else None,
    )
    pending = build_pending_handoff(
        handoff_id=data.get("handoff_id"),
        thread_id=str(data.get("thread_id") or ""),
        run_id=str(data.get("run_id") or ""),
        round_id=data.get("round_id") if isinstance(data.get("round_id"), str) else None,
        task_id=data.get("task_id") if isinstance(data.get("task_id"), str) else None,
        envelope=envelope,
        status=str(data.get("status") or "pending"),
        created_at=str(data.get("created_at") or "") or None,
        updated_at=str(data.get("updated_at") or "") or None,
        resolved_by_role=data.get("resolved_by_role") if isinstance(data.get("resolved_by_role"), str) else None,
        resolution_note=data.get("resolution_note") if isinstance(data.get("resolution_note"), str) else None,
    )
    if handoff_data:
        pending.handoff = dict(handoff_data)
    return pending


def _pending_handoff_file(thread_id: str, user_id: str | None, base_dir: Path | None = None) -> Path:
    if base_dir is not None:
        return base_dir / "pending_handoffs.jsonl"
    return get_paths().thread_dir(thread_id, user_id=user_id) / "audit" / "pending_handoffs.jsonl"


def record_pending_handoff(handoff: PendingHandoff, *, user_id: str | None = None, base_dir: Path | None = None) -> Path:
    path = _pending_handoff_file(handoff.thread_id, user_id, base_dir=base_dir)
    return append_jsonl_record(path, handoff.as_dict())


def list_pending_handoffs(
    *,
    thread_id: str,
    user_id: str | None,
    run_id: str | None = None,
    status: str | None = "pending",
    limit: int = 50,
    base_dir: Path | None = None,
) -> list[dict[str, Any]]:
    path = _pending_handoff_file(thread_id, user_id, base_dir=base_dir)
    text = read_jsonl_text(path)
    if text is None:
        return []
    expected_status = _status(status) if status else None
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
        handoff_id = str(row.get("handoff_id") or "")
        if not handoff_id:
            continue
        if handoff_id not in latest:
            order.append(handoff_id)
        latest[handoff_id] = row
    rows = [latest[item] for item in order]
    if expected_status is not None:
        rows = [row for row in rows if row.get("status") == expected_status]
    return rows[-limit:]


def resolve_pending_handoff(
    *,
    thread_id: str,
    user_id: str | None,
    handoff_id: str,
    status: str,
    resolved_by_role: str = "chair",
    resolution_note: str | None = None,
    run_id: str | None = None,
    base_dir: Path | None = None,
) -> PendingHandoff | None:
    if _status(status) == "pending":
        raise ValueError("Resolved pending handoff status must not be pending")
    rows = list_pending_handoffs(thread_id=thread_id, user_id=user_id, run_id=run_id, status=None, limit=500, base_dir=base_dir)
    current = next((row for row in rows if row.get("handoff_id") == handoff_id), None)
    if current is None:
        return None
    handoff = pending_handoff_from_dict(current)
    resolved = replace(
        handoff,
        status=_status(status),
        updated_at=_now_iso(),
        resolved_by_role=_clip(resolved_by_role, 64).lower() or "chair",
        resolution_note=_clean_optional(resolution_note),
    )
    record_pending_handoff(resolved, user_id=user_id, base_dir=base_dir)
    return resolved


def compact_pending_handoffs(handoffs: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for handoff in handoffs[-limit:]:
        compact.append(
            {
                "handoff_id": handoff.get("handoff_id"),
                "run_id": handoff.get("run_id"),
                "round_id": handoff.get("round_id"),
                "task_id": handoff.get("task_id"),
                "source_role": handoff.get("source_role"),
                "target_role": handoff.get("target_role"),
                "task_or_question": _clip(handoff.get("task_or_question"), _COMPACT_TEXT_LIMIT),
                "status": handoff.get("status"),
                "evidence_strength": handoff.get("evidence_strength") or "Unverified",
                "evidence_refs": _clean_list(handoff.get("evidence_refs"), limit=3),
                "artifact_refs": _clean_list(handoff.get("artifact_refs"), limit=3),
                "output_refs": _clean_list(handoff.get("output_refs"), limit=3),
                "updated_at": handoff.get("updated_at"),
                "ai_authored": True,
                "programmatic_dispatch": False,
                "auto_dispatch": False,
            }
        )
    return compact


__all__ = [
    "PENDING_HANDOFF_STATUSES",
    "PendingHandoff",
    "PendingHandoffStatus",
    "build_pending_handoff",
    "compact_pending_handoffs",
    "list_pending_handoffs",
    "pending_handoff_from_dict",
    "record_pending_handoff",
    "resolve_pending_handoff",
]
