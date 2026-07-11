"""AI-authored quality signals for Command Room.

Quality signals are advisory records for the lead AI / Chair. They are not
program verdicts and never dispatch reviewers or rework by themselves.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deerflow.command_room.file_records import append_jsonl_record, read_jsonl_text
from deerflow.config.paths import get_paths

QUALITY_RECOMMENDATIONS = frozenset({"continue", "needs_more_evidence", "needs_revision", "escalate", "stop"})
_BLOCKED_RECOMMENDATIONS = frozenset({"pass", "fail", "passed", "failed"})
_TEXT_LIMIT = 2000
_REF_LIMIT = 20
_COMPACT_RATIONALE_LIMIT = 240


@dataclass
class QualitySignal:
    signal_id: str
    thread_id: str
    run_id: str
    round_id: str | None
    task_id: str | None
    author_role: str
    recommendation: str
    rationale: str
    evidence_refs: list[str]
    capability_refs: list[str]
    capability_snapshot_version: int | None
    target_role: str
    created_at: str
    ai_authored: bool = True
    programmatic_decision: bool = False
    quality_verdict: None = None
    auto_rework: bool = False
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


def _recommendation(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    if text in _BLOCKED_RECOMMENDATIONS:
        raise ValueError("Quality signal recommendation must not be PASS/FAIL")
    if text not in QUALITY_RECOMMENDATIONS:
        allowed = ", ".join(sorted(QUALITY_RECOMMENDATIONS))
        raise ValueError(f"Unsupported quality signal recommendation: {text or '<empty>'}; expected one of {allowed}")
    return text


def build_quality_signal(
    *,
    thread_id: str,
    run_id: str,
    author_role: str,
    recommendation: str,
    rationale: str,
    round_id: str | None = None,
    task_id: str | None = None,
    evidence_refs: list[str] | None = None,
    capability_refs: list[str] | None = None,
    capability_snapshot_version: int | None = None,
    target_role: str = "Chair",
    signal_id: str | None = None,
    created_at: str | None = None,
) -> QualitySignal:
    author = _clip(author_role, 64).lower()
    if not author:
        raise ValueError("Quality signal author_role is required")
    reason = _clip(rationale)
    if not reason:
        raise ValueError("Quality signal rationale is required")
    target = _clip(target_role, 64) or "Chair"
    return QualitySignal(
        signal_id=_clip(signal_id, 128) or f"quality-{uuid.uuid4().hex}",
        thread_id=thread_id,
        run_id=run_id,
        round_id=_clean_optional(round_id, 128),
        task_id=_clean_optional(task_id, 128),
        author_role=author,
        recommendation=_recommendation(recommendation),
        rationale=reason,
        evidence_refs=_clean_list(evidence_refs),
        capability_refs=_clean_list(capability_refs),
        capability_snapshot_version=capability_snapshot_version,
        target_role=target,
        created_at=created_at or _now_iso(),
    )


def quality_signal_from_dict(data: dict[str, Any]) -> QualitySignal:
    return build_quality_signal(
        signal_id=data.get("signal_id"),
        thread_id=str(data.get("thread_id") or ""),
        run_id=str(data.get("run_id") or ""),
        round_id=data.get("round_id"),
        task_id=data.get("task_id"),
        author_role=str(data.get("author_role") or ""),
        recommendation=str(data.get("recommendation") or ""),
        rationale=str(data.get("rationale") or ""),
        evidence_refs=_clean_list(data.get("evidence_refs")),
        capability_refs=_clean_list(data.get("capability_refs")),
        capability_snapshot_version=data.get("capability_snapshot_version") if isinstance(data.get("capability_snapshot_version"), int) else None,
        target_role=str(data.get("target_role") or "Chair"),
        created_at=str(data.get("created_at") or "") or None,
    )


def _quality_file(thread_id: str, user_id: str | None, base_dir: Path | None = None) -> Path:
    if base_dir is not None:
        return base_dir / "quality_signals.jsonl"
    return get_paths().thread_dir(thread_id, user_id=user_id) / "audit" / "quality_signals.jsonl"


def record_quality_signal(signal: QualitySignal, *, user_id: str | None = None, base_dir: Path | None = None) -> Path:
    path = _quality_file(signal.thread_id, user_id, base_dir=base_dir)
    return append_jsonl_record(path, signal.as_dict())


def list_quality_signals(
    *,
    thread_id: str,
    user_id: str | None,
    run_id: str | None = None,
    round_id: str | None = None,
    task_id: str | None = None,
    limit: int = 50,
    base_dir: Path | None = None,
) -> list[dict[str, Any]]:
    path = _quality_file(thread_id, user_id, base_dir=base_dir)
    text = read_jsonl_text(path)
    if text is None:
        return []
    rows: list[dict[str, Any]] = []
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
        rows.append(row)
    return rows[-limit:]


def compact_quality_signals(signals: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for signal in signals[-limit:]:
        compact.append(
            {
                "signal_id": signal.get("signal_id"),
                "run_id": signal.get("run_id"),
                "round_id": signal.get("round_id"),
                "task_id": signal.get("task_id"),
                "author_role": signal.get("author_role"),
                "recommendation": signal.get("recommendation"),
                "target_role": signal.get("target_role") or "Chair",
                "rationale": _clip(signal.get("rationale"), _COMPACT_RATIONALE_LIMIT),
                "evidence_refs": _clean_list(signal.get("evidence_refs"), limit=3),
                "capability_refs": _clean_list(signal.get("capability_refs"), limit=3),
                "capability_snapshot_version": signal.get("capability_snapshot_version"),
                "created_at": signal.get("created_at"),
                "ai_authored": True,
                "quality_verdict": None,
                "auto_rework": False,
            }
        )
    return compact


__all__ = [
    "QUALITY_RECOMMENDATIONS",
    "QualitySignal",
    "build_quality_signal",
    "compact_quality_signals",
    "list_quality_signals",
    "quality_signal_from_dict",
    "record_quality_signal",
]
