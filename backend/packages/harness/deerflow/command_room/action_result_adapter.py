"""Lightweight adapters for Command Room action results.

The helpers in this module intentionally do not call the task runtime.  They
normalize observable terminal-event metadata/tool-runtime values into the small
:class:`ActionResult` contract used by ``Round.record_action_result``. Natural
subagent text is allowed, but remains summary-only and is not evidence by
format self-claim.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .round import ActionResult, RoundItemStatus

_STATUS_ALIASES = {
    "completed": RoundItemStatus.COMPLETED,
    "succeeded": RoundItemStatus.COMPLETED,
    "success": RoundItemStatus.COMPLETED,
    "failed": RoundItemStatus.FAILED,
    "error": RoundItemStatus.FAILED,
    "running": RoundItemStatus.RUNNING,
    "pending": RoundItemStatus.PENDING,
    "blocked": RoundItemStatus.BLOCKED,
}


def action_result_from_value(value: Any, *, default_action_id: str = "") -> ActionResult:
    """Convert a task/subagent-like value into an ``ActionResult``.

    Supported inputs are deliberately small and conservative:
    - mappings with ActionResult-like fields;
    - plain strings, kept as ``summary`` only with no invented evidence;
    - exceptions/errors, mapped to failed results with an unresolved ``error``.

    ``output_ref`` is preserved as output metadata and is never copied into
    ``evidence_refs``.
    """
    if isinstance(value, ActionResult):
        return value
    if isinstance(value, BaseException):
        return ActionResult(
            action_id=default_action_id,
            status=RoundItemStatus.FAILED,
            summary=str(value),
            error=str(value) or value.__class__.__name__,
        )
    if isinstance(value, Mapping):
        return _from_mapping(value, default_action_id=default_action_id)
    if isinstance(value, str):
        return ActionResult(
            action_id=default_action_id,
            status=RoundItemStatus.PENDING,
            summary=value,
        )
    return ActionResult(
        action_id=default_action_id,
        status=RoundItemStatus.PENDING,
        summary=str(value),
    )


def _from_mapping(value: Mapping[str, Any], *, default_action_id: str) -> ActionResult:
    status = _normalize_status(value.get("status"))
    error = _optional_str(value.get("error"))
    if error and status is None:
        status = RoundItemStatus.FAILED

    return ActionResult(
        action_id=_optional_str(value.get("action_id")) or default_action_id,
        description=_optional_str(value.get("description")) or "",
        status=status or RoundItemStatus.COMPLETED,
        summary=_optional_str(value.get("summary")) or "",
        evidence_refs=_string_list(value.get("evidence_refs")),
        output_ref=_optional_str(value.get("output_ref")),
        risks=_string_list(value.get("risks")),
        conflicts=_string_list(value.get("conflicts")),
        open_questions=_string_list(value.get("open_questions")),
        error=error,
    )


def _normalize_status(value: Any) -> RoundItemStatus | None:
    if isinstance(value, RoundItemStatus):
        return value
    if value is None:
        return None
    return _STATUS_ALIASES.get(str(value).strip().lower())


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [cleaned for item in value if (cleaned := str(item).strip())]
    cleaned = str(value).strip()
    return [cleaned] if cleaned else []


__all__ = ["action_result_from_value"]
