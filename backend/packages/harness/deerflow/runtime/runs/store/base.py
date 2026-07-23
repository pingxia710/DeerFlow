"""Abstract interface for run metadata storage.

RunManager depends on this interface. Implementations:
- MemoryRunStore: in-memory dict (development, tests)
- RunRepository: SQLAlchemy ORM

All methods accept an optional user_id for user isolation.
When user_id is None, no user filtering is applied (single-user mode).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class RunLease:
    thread_id: str
    run_id: str
    owner_worker_id: str
    lease_token: str
    generation: int
    lease_expires_at: datetime
    lease_heartbeat_at: datetime


@dataclass(frozen=True)
class CancelIntent:
    run_id: str
    action: str
    requested_at: str
    requested_by: str | None = None


@dataclass(frozen=True)
class CancelRequestResult:
    run_id: str
    status: str | None
    action: str | None
    accepted: bool
    terminal: bool = False
    terminal_reason: str | None = None


class RunStore(abc.ABC):
    @abc.abstractmethod
    async def put(
        self,
        run_id: str,
        *,
        thread_id: str,
        assistant_id: str | None = None,
        user_id: str | None = None,
        model_name: str | None = None,
        status: str = "pending",
        multitask_strategy: str = "reject",
        metadata: dict[str, Any] | None = None,
        kwargs: dict[str, Any] | None = None,
        error: str | None = None,
        created_at: str | None = None,
    ) -> None:
        pass

    @abc.abstractmethod
    async def get(
        self,
        run_id: str,
        *,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        pass

    @abc.abstractmethod
    async def list_by_thread(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
        limit: int = 100,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        pass

    @abc.abstractmethod
    async def update_status(
        self,
        run_id: str,
        status: str,
        *,
        error: str | None = None,
        terminal_reason: str | None = None,
    ) -> bool | None:
        """Update a run status.

        Returns ``False`` when the store can prove no row was updated. Older or
        lightweight stores may return ``None`` when they cannot report rowcount.
        """
        pass

    @abc.abstractmethod
    async def delete(self, run_id: str) -> None:
        pass

    async def delete_by_thread(self, thread_id: str, *, user_id: str | None = None) -> int:
        runs = await self.list_by_thread(thread_id, user_id=user_id, limit=100000)
        for run in runs:
            await self.delete(run["run_id"])
        return len(runs)

    async def delete_legacy_by_thread(self, thread_id: str) -> int:
        """Delete only ownerless legacy rows for *thread_id*.

        Implementations must match ``user_id IS NULL`` exactly. In particular,
        default-owned and concrete foreign-owned rows are not legacy rows.
        """
        raise NotImplementedError

    async def list_owners_by_thread(self, thread_id: str) -> set[str | None]:
        """Return every persisted owner marker for *thread_id*."""
        raise NotImplementedError

    @abc.abstractmethod
    async def update_model_name(
        self,
        run_id: str,
        model_name: str | None,
    ) -> None:
        """Update the model_name field for an existing run."""
        pass

    @abc.abstractmethod
    async def update_run_completion(
        self,
        run_id: str,
        *,
        status: str,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
        total_tokens: int = 0,
        llm_call_count: int = 0,
        lead_agent_tokens: int = 0,
        subagent_tokens: int = 0,
        middleware_tokens: int = 0,
        token_usage_by_model: dict[str, dict[str, int]] | None = None,
        message_count: int = 0,
        last_ai_message: str | None = None,
        first_human_message: str | None = None,
        error: str | None = None,
    ) -> bool | None:
        """Persist final completion fields.

        Returns ``False`` when the store can prove no row was updated.
        """
        pass

    async def update_run_progress(
        self,
        run_id: str,
        *,
        total_input_tokens: int | None = None,
        total_output_tokens: int | None = None,
        total_tokens: int | None = None,
        llm_call_count: int | None = None,
        lead_agent_tokens: int | None = None,
        subagent_tokens: int | None = None,
        middleware_tokens: int | None = None,
        token_usage_by_model: dict[str, dict[str, int]] | None = None,
        message_count: int | None = None,
        last_ai_message: str | None = None,
        first_human_message: str | None = None,
    ) -> None:
        """Persist a best-effort running snapshot without changing run status."""
        return None

    async def record_external_subagent_usage(
        self,
        run_id: str,
        *,
        source_run_id: str,
        model_name: str | None,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
    ) -> bool:
        """Atomically add one deduplicated external child-usage fact to a run."""
        raise NotImplementedError

    async def create_pending_run(self, run_id: str, *, thread_id: str, **kwargs: Any) -> dict[str, Any]:
        """Create a pending run row for lease/CAS callers.

        RunManager uses this before acquiring the thread active slot, so a
        store that supports leases must keep this row eligible for
        ``try_acquire_active_slot``.
        """
        await self.put(run_id, thread_id=thread_id, status="pending", **kwargs)
        row = await self.get(run_id)
        if row is None:
            raise RuntimeError(f"pending run {run_id!r} was not persisted")
        return row

    async def reserve_command_room_wake(
        self,
        *,
        wake_id: str,
        thread_id: str,
        assistant_id: str,
        user_id: str | None,
        metadata: dict[str, Any],
        kwargs: dict[str, Any],
        multitask_strategy: str,
        model_name: str | None,
    ) -> tuple[dict[str, Any], bool]:
        """Atomically insert or read the canonical Command Room wake run."""
        raise NotImplementedError

    async def get_by_command_room_wake_id(self, wake_id: str) -> dict[str, Any] | None:
        """Return the globally canonical wake run without owner filtering."""
        raise NotImplementedError

    async def try_acquire_active_slot(
        self,
        thread_id: str,
        run_id: str,
        *,
        owner_worker_id: str,
        lease_token: str | None = None,
        lease_expires_at: datetime | None = None,
        now: datetime | None = None,
    ) -> RunLease | None:
        raise NotImplementedError

    async def heartbeat_lease(
        self,
        run_id: str,
        *,
        lease_token: str,
        generation: int,
        lease_expires_at: datetime | None = None,
        metadata_updates: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> bool:
        raise NotImplementedError

    async def request_cancel(
        self,
        run_id: str,
        action: str,
        *,
        requested_by: str | None = None,
        now: datetime | None = None,
    ) -> CancelRequestResult | None:
        raise NotImplementedError

    async def consume_cancel_intent(
        self,
        run_id: str,
        *,
        lease_token: str,
        generation: int,
        now: datetime | None = None,
    ) -> CancelIntent | None:
        raise NotImplementedError

    async def cas_status(
        self,
        run_id: str,
        *,
        from_statuses: set[str] | tuple[str, ...] | list[str],
        to_status: str,
        lease_token: str,
        generation: int,
        terminal_reason: str | None = None,
        error: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        raise NotImplementedError

    async def complete_run(
        self,
        run_id: str,
        *,
        from_statuses: set[str] | tuple[str, ...] | list[str],
        terminal_status: str,
        lease_token: str,
        generation: int,
        terminal_reason: str | None = None,
        error: str | None = None,
        now: datetime | None = None,
        completion_fields: dict[str, Any] | None = None,
    ) -> bool:
        raise NotImplementedError

    async def backfill_completion_metadata(
        self,
        run_id: str,
        *,
        terminal_status: str,
        lease_token: str,
        generation: int,
        terminal_reason: str | None = None,
        metadata: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> bool:
        raise NotImplementedError

    async def release_active_slot(
        self,
        thread_id: str,
        run_id: str,
        *,
        lease_token: str,
        generation: int,
    ) -> bool:
        raise NotImplementedError

    async def list_expired_active_leases(self, now: datetime) -> list[RunLease]:
        raise NotImplementedError

    async def recover_expired_lease(
        self,
        run_id: str,
        *,
        generation: int,
        terminal_status: str = "error",
        terminal_reason: str | None = None,
        recovery_worker_id: str = "recovery",
        recovery_lease_token: str | None = None,
        now: datetime | None = None,
        error: str | None = None,
    ) -> bool:
        raise NotImplementedError

    @abc.abstractmethod
    async def list_pending(self, *, before: str | None = None) -> list[dict[str, Any]]:
        pass

    @abc.abstractmethod
    async def list_inflight(self, *, before: str | None = None) -> list[dict[str, Any]]:
        """Return persisted runs that are still pending or active."""
        pass

    @abc.abstractmethod
    async def aggregate_tokens_by_thread(
        self,
        thread_id: str,
        *,
        include_active: bool = False,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate token usage for completed runs in a thread.

        Returns a dict with keys: total_tokens, total_input_tokens,
        total_output_tokens, total_runs, by_model (model_name → {tokens, runs}),
        by_caller ({lead_agent, subagent, middleware}).
        """
        pass
