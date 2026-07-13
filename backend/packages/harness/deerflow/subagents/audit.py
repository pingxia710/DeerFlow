"""Append-only factual audit records for one-shot subagent handoffs."""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deerflow.command_room.file_records import append_jsonl_record
from deerflow.config.paths import get_paths

logger = logging.getLogger(__name__)

_SAFE_ACTION_RESULT_KEYS = {
    "action_id",
    "description",
    "status",
    "evidence_refs",
    "output_ref",
    "risks",
    "conflicts",
    "open_questions",
}


def _sha256_text(text: str | None) -> str | None:
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text is not None else None


def _compact_action_result(action_result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not action_result:
        return None
    compact = {key: action_result[key] for key in _SAFE_ACTION_RESULT_KEYS if key in action_result}
    for key in ("summary", "error"):
        value = action_result.get(key)
        if value is not None:
            text = str(value)
            compact[f"{key}_sha256"] = _sha256_text(text)
            compact[f"{key}_chars"] = len(text)
    return compact


def _audit_file(thread_id: str | None, user_id: str | None, base_dir: Path | None = None) -> Path:
    if base_dir is not None:
        return base_dir / "subagent_handoffs.jsonl"
    paths = get_paths()
    if thread_id:
        try:
            return paths.thread_dir(thread_id, user_id=user_id) / "audit" / "subagent_handoffs.jsonl"
        except ValueError:
            logger.debug("Unsafe thread/user id for subagent audit path; using global audit", exc_info=True)
    return paths.base_dir / "audit" / "subagent_handoffs.jsonl"


def record_subagent_handoff(
    *,
    thread_id: str | None,
    run_id: str | None,
    task_id: str,
    trace_id: str | None,
    user_id: str | None,
    subagent_type: str,
    description: str,
    prompt: str,
    status: str,
    result: str | None = None,
    error: str | None = None,
    usage: dict[str, int] | None = None,
    action_result: dict[str, Any] | None = None,
    base_dir: Path | None = None,
) -> Path | None:
    """Record identities, lifecycle facts, sizes, and hashes without parsing prose."""

    try:
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "thread_id": thread_id or "unknown",
            "run_id": run_id,
            "task_id": task_id,
            "trace_id": trace_id,
            "user_id": user_id,
            "subagent_type": subagent_type,
            "description": description,
            "status": status,
            "prompt_sha256": _sha256_text(prompt),
            "prompt_chars": len(prompt),
            "result_sha256": _sha256_text(result),
            "result_chars": len(result or ""),
            "error_sha256": _sha256_text(error),
            "error_chars": len(error or ""),
            "usage": usage,
            "action_result": _compact_action_result(action_result),
        }
        return append_jsonl_record(_audit_file(thread_id, user_id, base_dir=base_dir), record)
    except Exception:
        logger.debug("Failed to write subagent handoff audit record", exc_info=True)
        return None


__all__ = ["record_subagent_handoff"]
