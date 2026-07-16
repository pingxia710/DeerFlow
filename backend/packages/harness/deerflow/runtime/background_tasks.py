"""Runtime-neutral contracts for Command Room background AI work."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

CommandRoomBackgroundStatus = Literal["completed", "failed", "timed_out", "cancelled"]


@dataclass(frozen=True)
class CommandRoomBackgroundOutcome:
    """Factual result returned by one background child AI."""

    status: CommandRoomBackgroundStatus
    result: str | None = None
    error: str | None = None
    container_artifact_written: bool | None = None


@dataclass(frozen=True)
class CommandRoomBackgroundJob:
    """One child task plus the information required to wake its Chair."""

    thread_id: str
    source_run_id: str
    task_id: str
    description: str
    subagent_type: str
    execute: Callable[[], Awaitable[CommandRoomBackgroundOutcome]] = field(repr=False)
    wake_context: Mapping[str, Any] = field(default_factory=dict, repr=False)
    command_room_container: str | None = None
    container_artifact_path: str | None = None
    delivery_cycle_index: int | None = None
    work_package_id: str | None = None


class CommandRoomBackgroundDispatcher(Protocol):
    """Gateway-owned service injected into a Command Room tool runtime."""

    async def dispatch(self, job: CommandRoomBackgroundJob) -> None:
        """Accept a job without waiting for its child process to finish."""


__all__ = [
    "CommandRoomBackgroundDispatcher",
    "CommandRoomBackgroundJob",
    "CommandRoomBackgroundOutcome",
    "CommandRoomBackgroundStatus",
]
