"""Fact-only terminal payload for one-shot task events."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class TaskActionResult:
    """Observed terminal task facts, kept separate from planning state."""

    action_id: str
    description: str
    status: str
    terminal_reason: str | None
    summary: str
    evidence_refs: list[str]
    output_ref: str | None = None
    risks: list[str] | None = None
    conflicts: list[str] | None = None
    open_questions: list[str] | None = None
    error: str | None = None


_STATUS_ALIASES = {
    "completed": "completed",
    "succeeded": "completed",
    "success": "completed",
    "failed": "failed",
    "error": "failed",
    "pending": "pending",
    "running": "running",
    "blocked": "blocked",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "timed_out": "timed_out",
    "timeout": "timed_out",
}


def _terminal_status(status: str, *, has_error: bool) -> str:
    return _STATUS_ALIASES.get(status.strip().lower(), "failed" if has_error else "pending")


def _default_terminal_reason(status: str, *, has_error: bool) -> str | None:
    if status == "cancelled":
        return "user_cancelled"
    if status == "timed_out":
        return "timed_out"
    if status == "blocked":
        return "boundary_blocked"
    if status == "failed" and has_error:
        return "failed"
    return None


def _summary_from_result(result: Any) -> str:
    if isinstance(result, Mapping):
        summary = result.get("summary")
        return str(summary) if summary is not None else ""
    return "" if result is None else str(result)


def task_action_result_from_terminal_event(
    *,
    task_id: str,
    status: str,
    description: str = "",
    result: Any = None,
    error: Any = None,
    terminal_reason: str | None = None,
    observed_evidence_refs: Sequence[str] | None = None,
) -> TaskActionResult:
    """Build a fact-only terminal task payload.

    Plain string subagent output is kept as the ``summary`` only; it is not
    promoted into ``evidence_refs``.  Dict-like terminal values are treated as
    untrusted when they are just the model's returned text parsed as a mapping:
    the task runtime/adapter may keep their descriptive fields, but does not
    promote their claimed evidence refs unless observable metadata/tool output
    supplies them through a trusted adapter path.
    """
    error_text = str(error) if error is not None else None
    normalized_status = _terminal_status(status, has_error=bool(error_text))
    return TaskActionResult(
        action_id=task_id,
        description=description,
        status=normalized_status,
        terminal_reason=terminal_reason or _default_terminal_reason(normalized_status, has_error=bool(error_text)),
        summary=_summary_from_result(result) or error_text or "",
        evidence_refs=list(dict.fromkeys(ref.strip() for ref in observed_evidence_refs or [] if isinstance(ref, str) and ref.strip())),
        risks=[],
        conflicts=[],
        open_questions=[],
        error=error_text,
    )


def task_action_result_event(action_result: TaskActionResult) -> dict[str, Any]:
    """Return stream-writer metadata for a terminal task payload."""
    payload = asdict(action_result)
    return {"type": "task_action_result", "action_result": payload}


__all__ = ["TaskActionResult", "task_action_result_event", "task_action_result_from_terminal_event"]
