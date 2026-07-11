"""Chair-accepted role state summaries for Command Room."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deerflow.command_room.file_records import append_jsonl_record, read_jsonl_text
from deerflow.config.paths import get_paths

_TEXT_LIMIT = 2000
_COMPACT_TEXT_LIMIT = 240
_REF_LIMIT = 20


@dataclass
class RoleState:
    state_id: str
    thread_id: str
    role_name: str
    summary: str
    current_focus: str | None
    open_questions: list[str]
    accepted_signals: list[str]
    evidence_refs: list[str]
    artifact_refs: list[str]
    run_id: str | None
    round_id: str | None
    updated_by_role: str
    target_role: str
    updated_at: str
    ai_authored: bool = True
    programmatic_decision: bool = False
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


def build_role_state(
    *,
    thread_id: str,
    role_name: str,
    summary: str,
    current_focus: str | None = None,
    open_questions: list[str] | None = None,
    accepted_signals: list[str] | None = None,
    evidence_refs: list[str] | None = None,
    artifact_refs: list[str] | None = None,
    run_id: str | None = None,
    round_id: str | None = None,
    updated_by_role: str = "chair",
    target_role: str = "Chair",
    state_id: str | None = None,
    updated_at: str | None = None,
) -> RoleState:
    role = _clip(role_name, 64).lower()
    if not role:
        raise ValueError("Role state role_name is required")
    cleaned_summary = _clip(summary)
    if not cleaned_summary:
        raise ValueError("Role state summary is required")
    return RoleState(
        state_id=_clip(state_id, 128) or f"role-state-{uuid.uuid4().hex}",
        thread_id=thread_id,
        role_name=role,
        summary=cleaned_summary,
        current_focus=_clean_optional(current_focus),
        open_questions=_clean_list(open_questions),
        accepted_signals=_clean_list(accepted_signals),
        evidence_refs=_clean_list(evidence_refs),
        artifact_refs=_clean_list(artifact_refs),
        run_id=_clean_optional(run_id, 128),
        round_id=_clean_optional(round_id, 128),
        updated_by_role=_clip(updated_by_role, 64).lower() or "chair",
        target_role=_clip(target_role, 64) or "Chair",
        updated_at=updated_at or _now_iso(),
    )


def role_state_from_dict(data: dict[str, Any]) -> RoleState:
    return build_role_state(
        state_id=data.get("state_id"),
        thread_id=str(data.get("thread_id") or ""),
        role_name=str(data.get("role_name") or ""),
        summary=str(data.get("summary") or ""),
        current_focus=data.get("current_focus") if isinstance(data.get("current_focus"), str) else None,
        open_questions=_clean_list(data.get("open_questions")),
        accepted_signals=_clean_list(data.get("accepted_signals")),
        evidence_refs=_clean_list(data.get("evidence_refs")),
        artifact_refs=_clean_list(data.get("artifact_refs")),
        run_id=data.get("run_id") if isinstance(data.get("run_id"), str) else None,
        round_id=data.get("round_id") if isinstance(data.get("round_id"), str) else None,
        updated_by_role=str(data.get("updated_by_role") or "chair"),
        target_role=str(data.get("target_role") or "Chair"),
        updated_at=str(data.get("updated_at") or "") or None,
    )


def _role_state_file(thread_id: str, user_id: str | None, base_dir: Path | None = None) -> Path:
    if base_dir is not None:
        return base_dir / "role_state.jsonl"
    return get_paths().thread_dir(thread_id, user_id=user_id) / "audit" / "role_state.jsonl"


def record_role_state(state: RoleState, *, user_id: str | None = None, base_dir: Path | None = None) -> Path:
    path = _role_state_file(state.thread_id, user_id, base_dir=base_dir)
    return append_jsonl_record(path, state.as_dict())


def list_role_states(
    *,
    thread_id: str,
    user_id: str | None,
    role_name: str | None = None,
    limit: int = 20,
    base_dir: Path | None = None,
) -> list[dict[str, Any]]:
    path = _role_state_file(thread_id, user_id, base_dir=base_dir)
    text = read_jsonl_text(path)
    if text is None:
        return []
    latest: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    expected_role = _clip(role_name, 64).lower() if role_name else None
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("thread_id") != thread_id:
            continue
        role = str(row.get("role_name") or "").lower()
        if not role:
            continue
        if expected_role is not None and role != expected_role:
            continue
        if role not in latest:
            order.append(role)
        latest[role] = row
    return [latest[item] for item in order][-limit:]


def compact_role_states(states: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for state in states[-limit:]:
        compact.append(
            {
                "state_id": state.get("state_id"),
                "role_name": state.get("role_name"),
                "run_id": state.get("run_id"),
                "round_id": state.get("round_id"),
                "summary": _clip(state.get("summary"), _COMPACT_TEXT_LIMIT),
                "current_focus": _clip(state.get("current_focus"), _COMPACT_TEXT_LIMIT),
                "open_questions": _clean_list(state.get("open_questions"), limit=3),
                "accepted_signals": _clean_list(state.get("accepted_signals"), limit=3),
                "evidence_refs": _clean_list(state.get("evidence_refs"), limit=3),
                "artifact_refs": _clean_list(state.get("artifact_refs"), limit=3),
                "updated_by_role": state.get("updated_by_role") or "chair",
                "target_role": state.get("target_role") or "Chair",
                "updated_at": state.get("updated_at"),
                "ai_authored": True,
                "programmatic_decision": False,
                "auto_dispatch": False,
            }
        )
    return compact


__all__ = [
    "RoleState",
    "build_role_state",
    "compact_role_states",
    "list_role_states",
    "record_role_state",
    "role_state_from_dict",
]
