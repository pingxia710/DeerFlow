"""Run status and disconnect mode enums."""

from enum import StrEnum
from typing import Any


class RunStatus(StrEnum):
    """Lifecycle status of a single run."""

    pending = "pending"
    running = "running"
    success = "success"
    error = "error"
    timeout = "timeout"
    interrupted = "interrupted"


ACTIVE_RUN_STATUS_VALUES = frozenset({"running", "cancelling", "rolling_back"})
TERMINAL_RUN_STATUS_VALUES = frozenset(
    {
        "success",
        "error",
        "timeout",
        "interrupted",
        "cancelled",
        "timed_out",
        "rolled_back",
        "rollback_failed",
    }
)


def _status_value(status: Any) -> str | None:
    if status is None:
        return None
    return getattr(status, "value", status)


def is_active_status(status: Any) -> bool:
    return _status_value(status) in ACTIVE_RUN_STATUS_VALUES


def is_terminal_status(status: Any) -> bool:
    return _status_value(status) in TERMINAL_RUN_STATUS_VALUES


class DisconnectMode(StrEnum):
    """Behaviour when the SSE consumer disconnects."""

    cancel = "cancel"
    continue_ = "continue"
