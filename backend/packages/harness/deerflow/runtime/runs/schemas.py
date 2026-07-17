"""Run status and Command Room wake-admission contracts."""

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from .manager import RunRecord


class CommandRoomWakeIdentityConflict(Exception):
    """A wake key resolved to a run with a different immutable identity."""


class CommandRoomWakeAdmissionUnavailable(Exception):
    """The durable wake reservation could not be determined safely."""


class WakeAdmissionOutcome(StrEnum):
    LEASE_WON = "lease_won"
    ACTIVE = "active"
    SUCCEEDED = "succeeded"
    TERMINAL_FAILURE = "terminal_failure"
    ACTIVE_SLOT_BLOCKED = "active_slot_blocked"
    ADMISSION_UNAVAILABLE = "admission_unavailable"


@dataclass(frozen=True)
class CommandRoomWakeAdmission:
    """Private, server-authored payload for a Command Room wake reservation."""

    wake_id: str
    thread_id: str
    user_id: str | None
    assistant_id: str
    source_run_id: str
    source_task_id: str
    metadata: dict[str, Any]
    kwargs: dict[str, Any]
    multitask_strategy: str = "reject"
    model_name: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.wake_id, str) or not self.wake_id or self.wake_id != self.wake_id.strip() or len(self.wake_id) > 64:
            raise ValueError("command_room wake_id must be non-blank and at most 64 characters")
        try:
            parsed_wake_id = UUID(self.wake_id)
        except ValueError as exc:
            raise ValueError("command_room wake_id must be a canonical uuid4") from exc
        if parsed_wake_id.version != 4 or str(parsed_wake_id) != self.wake_id:
            raise ValueError("command_room wake_id must be a canonical uuid4")
        if self.assistant_id != "command-room":
            raise ValueError("command_room wake admission requires the command-room assistant")
        if not self.thread_id or not self.source_run_id or not self.source_task_id:
            raise ValueError("command_room wake admission requires thread, source run, and source task identities")
        object.__setattr__(self, "metadata", deepcopy(self.metadata))
        object.__setattr__(self, "kwargs", deepcopy(self.kwargs))

    def persisted_metadata(self) -> dict[str, Any]:
        """Return the immutable identity metadata stored with the canonical run."""
        return {
            **self.metadata,
            "command_room_wakeup": True,
            "source_run_id": self.source_run_id,
            "source_task_id": self.source_task_id,
            "command_room_wake_id": self.wake_id,
        }


@dataclass(frozen=True)
class WakeAdmissionResult:
    """Closed result returned by the durable wake-admission boundary."""

    record: "RunRecord | None"
    outcome: WakeAdmissionOutcome
    created: bool
    lease_token: str | None = None
    generation: int | None = None

    @property
    def should_start_worker(self) -> bool:
        return self.outcome is WakeAdmissionOutcome.LEASE_WON


class RunStatus(StrEnum):
    """Lifecycle status of a single run."""

    pending = "pending"
    running = "running"
    success = "success"
    error = "error"
    timeout = "timeout"
    interrupted = "interrupted"


ACTIVE_RUN_STATUS_VALUES = frozenset({"running", "cancelling", "rolling_back"})
INFLIGHT_RUN_STATUS_VALUES = frozenset({RunStatus.pending.value, *ACTIVE_RUN_STATUS_VALUES})
TERMINAL_RUN_STATUS_VALUES = frozenset(
    {
        "success",
        "error",
        "timeout",
        "interrupted",
        "cancelled",
        "timed_out",
        "boundary_stopped",
        "worker_lost",
        "rolled_back",
        "rollback_failed",
    }
)


def run_status_value(status: Any) -> str | None:
    if status is None:
        return None
    value = getattr(status, "value", status)
    return value if isinstance(value, str) else str(value)


def is_active_status(status: Any) -> bool:
    return run_status_value(status) in ACTIVE_RUN_STATUS_VALUES


def is_inflight_status(status: Any) -> bool:
    return run_status_value(status) in INFLIGHT_RUN_STATUS_VALUES


def is_terminal_status(status: Any) -> bool:
    return run_status_value(status) in TERMINAL_RUN_STATUS_VALUES


class DisconnectMode(StrEnum):
    """Behaviour when the SSE consumer disconnects."""

    cancel = "cancel"
    continue_ = "continue"
