"""Helpers for exposing task-tool terminal results as Command Room ActionResult.

This module is intentionally independent from Round persistence.  It lets the
subagent/task layer attach a small structured result near the terminal event
without changing the public ``task() -> str`` tool contract.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .action_result_adapter import action_result_from_value
from .round import ActionResult


def task_action_result_from_terminal_event(
    *,
    task_id: str,
    status: str,
    description: str = "",
    result: Any = None,
    error: Any = None,
) -> ActionResult:
    """Build an ``ActionResult`` from a terminal task-tool outcome.

    Plain string subagent output is kept as the ``summary`` only; it is not
    promoted into ``evidence_refs``.  Dict-like terminal values are treated as
    untrusted when they are just the model's returned text parsed as a mapping:
    the task runtime/adapter may keep their descriptive fields, but does not
    promote their claimed evidence refs unless observable metadata/tool output
    supplies them through a trusted adapter path.
    """
    payload: dict[str, Any]
    if isinstance(result, dict):
        payload = dict(result)
        payload.setdefault("summary", result.get("summary", ""))
        claimed_evidence = payload.pop("evidence_refs", None)
        if claimed_evidence:
            risks = payload.get("risks")
            if isinstance(risks, list):
                payload["risks"] = [*risks, "untrusted model-text evidence_refs not promoted"]
            elif risks:
                payload["risks"] = [str(risks), "untrusted model-text evidence_refs not promoted"]
            else:
                payload["risks"] = ["untrusted model-text evidence_refs not promoted"]
    else:
        payload = {"summary": "" if result is None else str(result)}
    payload.setdefault("action_id", task_id)
    payload.setdefault("description", description)
    payload["status"] = status
    if error is not None:
        payload["error"] = str(error)
        payload.setdefault("summary", str(error))
    return action_result_from_value(payload, default_action_id=task_id)


def task_action_result_event(action_result: ActionResult) -> dict[str, Any]:
    """Return stream-writer metadata for a terminal task ``ActionResult``."""
    payload = asdict(action_result)
    payload["status"] = action_result.status.value
    return {"type": "task_action_result", "action_result": payload}


__all__ = ["task_action_result_event", "task_action_result_from_terminal_event"]
