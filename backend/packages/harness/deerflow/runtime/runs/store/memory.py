"""In-memory RunStore with the same active-slot lease/CAS contract as SQL.

Used when database.backend=memory (default) and in tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from deerflow.runtime.runs.schemas import is_active_status, is_inflight_status, is_terminal_status
from deerflow.runtime.runs.store.base import CancelIntent, CancelRequestResult, RunLease, RunStore
from deerflow.runtime.user_context import DEFAULT_USER_ID

_DEFAULT_LEASE_TTL = timedelta(seconds=30)


class MemoryRunStore(RunStore):
    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}
        # Secondary index: thread_id -> insertion-ordered run_id set (a dict is
        # used as an ordered set), maintained in lockstep with ``_runs`` so
        # per-thread queries avoid O(total in-memory runs) full scans. Mirrors
        # the index ``RunManager`` keeps over its own in-memory records.
        self._runs_by_thread: dict[str, dict[str, None]] = {}
        self._active_slots: dict[str, RunLease] = {}
        self._next_generation_by_thread: dict[str, int] = {}

    @staticmethod
    def _now(now: datetime | None = None) -> datetime:
        if now is None:
            return datetime.now(UTC)
        if now.tzinfo is None:
            return now.replace(tzinfo=UTC)
        return now

    @staticmethod
    def _lease_expires_at(now: datetime, lease_expires_at: datetime | None) -> datetime:
        if lease_expires_at is None:
            return now + _DEFAULT_LEASE_TTL
        if lease_expires_at.tzinfo is None:
            return lease_expires_at.replace(tzinfo=UTC)
        return lease_expires_at

    @staticmethod
    def _terminal_reason(run: dict[str, Any]) -> str | None:
        reason = run.get("terminal_reason")
        if reason is not None:
            return reason
        metadata = run.get("metadata")
        return metadata.get("terminal_reason") if isinstance(metadata, dict) else None

    @staticmethod
    def _set_terminal_reason(run: dict[str, Any], terminal_reason: str | None) -> None:
        if terminal_reason is not None:
            run["terminal_reason"] = terminal_reason

    def _index_run(self, run_id: str, thread_id: str) -> None:
        """Register *run_id* under *thread_id* in the secondary index."""
        self._runs_by_thread.setdefault(thread_id, {})[run_id] = None

    def _unindex_run(self, run_id: str, thread_id: str) -> None:
        """Drop *run_id* from the *thread_id* bucket, removing the bucket when empty."""
        bucket = self._runs_by_thread.get(thread_id)
        if bucket is not None:
            bucket.pop(run_id, None)
            if not bucket:
                self._runs_by_thread.pop(thread_id, None)

    async def put(
        self,
        run_id,
        *,
        thread_id,
        assistant_id=None,
        user_id=None,
        model_name=None,
        status="pending",
        multitask_strategy="reject",
        metadata=None,
        kwargs=None,
        error=None,
        created_at=None,
    ):
        now = datetime.now(UTC).isoformat()
        metadata = metadata or {}
        row = {
            "run_id": run_id,
            "thread_id": thread_id,
            "assistant_id": assistant_id,
            "user_id": user_id,
            "model_name": model_name,
            "status": status,
            "multitask_strategy": multitask_strategy,
            "metadata": metadata,
            "kwargs": kwargs or {},
            "error": error,
            "created_at": created_at or now,
            "updated_at": now,
        }
        previous = self._runs.get(run_id)
        if previous is not None:
            if previous.get("command_room_wake_id") is not None:
                for key in (
                    "thread_id",
                    "assistant_id",
                    "user_id",
                    "multitask_strategy",
                    "metadata",
                    "kwargs",
                    "command_room_wake_id",
                ):
                    row[key] = previous.get(key)
        for key in ("owner_worker_id", "lease_token", "generation", "lease_expires_at", "lease_heartbeat_at"):
            if key in metadata:
                row[key] = metadata[key]
        self._runs[run_id] = row
        self._index_run(run_id, thread_id)

    async def create_pending_run(self, run_id: str, *, thread_id: str, **kwargs: Any) -> dict[str, Any]:
        await self.put(run_id, thread_id=thread_id, status="pending", **kwargs)
        return self._runs[run_id]

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
        existing = await self.get_by_command_room_wake_id(wake_id)
        if existing is not None:
            return existing, False
        run_id = str(uuid4())
        await self.put(
            run_id,
            thread_id=thread_id,
            assistant_id=assistant_id,
            user_id=user_id,
            model_name=model_name,
            status="pending",
            multitask_strategy=multitask_strategy,
            metadata=metadata,
            kwargs=kwargs,
        )
        self._runs[run_id]["command_room_wake_id"] = wake_id
        return self._runs[run_id], True

    async def get_by_command_room_wake_id(self, wake_id: str) -> dict[str, Any] | None:
        return next(
            (run for run in self._runs.values() if run.get("command_room_wake_id") == wake_id),
            None,
        )

    async def get(self, run_id, *, user_id=None):
        run = self._runs.get(run_id)
        if run is None:
            return None
        if user_id is not None and run.get("user_id") != user_id:
            return None
        return run

    async def list_by_thread(self, thread_id, *, user_id=None, limit=100, before=None):
        # Use the thread index for an O(runs-in-thread) lookup instead of
        # scanning every run. ``self._runs.get`` is defense-in-depth: it drops a
        # stale id still in the index but already gone from ``_runs``.
        run_ids = self._runs_by_thread.get(thread_id)
        if not run_ids:
            return []
        results = [run for run_id in run_ids if (run := self._runs.get(run_id)) is not None and (user_id is None or run.get("user_id") == user_id)]
        results.sort(key=lambda r: (r["created_at"], r["run_id"]), reverse=True)
        if before is not None:
            cursor_index = next(
                (index for index, row in enumerate(results) if row["run_id"] == before),
                None,
            )
            if cursor_index is None:
                return []
            results = results[cursor_index + 1 :]
        return results[:limit]

    async def update_status(self, run_id, status, *, error=None, terminal_reason=None):
        if run_id in self._runs:
            self._runs[run_id]["status"] = status
            self._set_terminal_reason(self._runs[run_id], terminal_reason)
            if error is not None:
                self._runs[run_id]["error"] = error
            self._runs[run_id]["updated_at"] = datetime.now(UTC).isoformat()
            return True
        return False

    async def update_model_name(self, run_id, model_name):
        if run_id in self._runs:
            self._runs[run_id]["model_name"] = model_name
            self._runs[run_id]["updated_at"] = datetime.now(UTC).isoformat()

    async def delete(self, run_id):
        run = self._runs.pop(run_id, None)
        if run is not None:
            lease = self._active_slots.get(run["thread_id"])
            if lease is not None and lease.run_id == run_id:
                self._active_slots.pop(run["thread_id"], None)
            self._unindex_run(run_id, run["thread_id"])

    async def delete_by_thread(self, thread_id, *, user_id=None):
        run_ids = list(self._runs_by_thread.get(thread_id, {}))
        deleted = 0
        for run_id in run_ids:
            run = self._runs.get(run_id)
            if run is None or (user_id is not None and run.get("user_id") != user_id):
                continue
            await self.delete(run_id)
            deleted += 1
        return deleted

    async def delete_legacy_by_thread(self, thread_id: str) -> int:
        run_ids = list(self._runs_by_thread.get(thread_id, {}))
        deleted = 0
        for run_id in run_ids:
            run = self._runs.get(run_id)
            if run is None or run.get("user_id") is not None:
                continue
            await self.delete(run_id)
            deleted += 1
        return deleted

    async def claim_legacy_by_thread(self, thread_id: str, owner_user_id: str) -> int:
        claimed = 0
        for run_id in self._runs_by_thread.get(thread_id, {}):
            run = self._runs.get(run_id)
            if run is None or run.get("user_id") not in {None, DEFAULT_USER_ID}:
                continue
            run["user_id"] = owner_user_id
            run["updated_at"] = datetime.now(UTC).isoformat()
            claimed += 1
        return claimed

    async def list_owners_by_thread(self, thread_id: str) -> set[str | None]:
        return {run.get("user_id") for run_id in self._runs_by_thread.get(thread_id, {}) if (run := self._runs.get(run_id)) is not None}

    async def update_run_completion(self, run_id, *, status, **kwargs):
        if run_id in self._runs:
            run = self._runs[run_id]
            metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
            pending = metadata.pop("_pending_external_subagent_usage", None)
            if isinstance(pending, dict):
                for field, key in (("total_input_tokens", "input_tokens"), ("total_output_tokens", "output_tokens"), ("total_tokens", "total_tokens"), ("subagent_tokens", "total_tokens")):
                    kwargs[field] = (kwargs.get(field) or 0) + (pending.get(key) or 0)
                by_model = kwargs.get("token_usage_by_model") or {}
                by_model = {name: dict(usage) for name, usage in by_model.items()}
                for name, usage in (pending.get("by_model") or {}).items():
                    bucket = by_model.setdefault(name, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
                    for key in bucket:
                        bucket[key] += usage.get(key, 0) or 0
                kwargs["token_usage_by_model"] = by_model
            run["status"] = status
            for key, value in kwargs.items():
                if value is not None:
                    run[key] = value
            run["updated_at"] = datetime.now(UTC).isoformat()
            return True
        return False

    async def update_run_progress(self, run_id, **kwargs):
        if run_id in self._runs and self._runs[run_id].get("status") == "running":
            for key, value in kwargs.items():
                if value is not None:
                    self._runs[run_id][key] = value
            self._runs[run_id]["updated_at"] = datetime.now(UTC).isoformat()

    async def record_external_subagent_usage(
        self,
        run_id,
        *,
        source_run_id,
        model_name,
        input_tokens,
        output_tokens,
        total_tokens,
    ):
        run = self._runs.get(run_id)
        if run is None:
            return False
        metadata = run.setdefault("metadata", {})
        source_ids = metadata.setdefault("_external_subagent_usage_sources", [])
        if source_run_id in source_ids:
            return False
        source_ids.append(source_run_id)
        pending = metadata.setdefault(
            "_pending_external_subagent_usage",
            {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "by_model": {}},
        )
        for field, key, value in (
            ("total_input_tokens", "input_tokens", input_tokens),
            ("total_output_tokens", "output_tokens", output_tokens),
            ("total_tokens", "total_tokens", total_tokens),
        ):
            run[field] = run.get(field, 0) + value
            pending[key] = pending.get(key, 0) + value
        run["subagent_tokens"] = run.get("subagent_tokens", 0) + total_tokens
        bucket = (run.setdefault("token_usage_by_model", {})).setdefault(
            model_name or "unknown",
            {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        )
        pending_bucket = pending["by_model"].setdefault(model_name or "unknown", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
        for key, value in (("input_tokens", input_tokens), ("output_tokens", output_tokens), ("total_tokens", total_tokens)):
            bucket[key] += value
            pending_bucket[key] += value
        run["updated_at"] = datetime.now(UTC).isoformat()
        return True

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
        run = self._runs.get(run_id)
        if run is None or run.get("thread_id") != thread_id or run.get("status") != "pending":
            return None
        if thread_id in self._active_slots:
            return None
        now_dt = self._now(now)
        generation = self._next_generation_by_thread.get(thread_id, 1)
        self._next_generation_by_thread[thread_id] = generation + 1
        token = lease_token or uuid4().hex
        expires_at = self._lease_expires_at(now_dt, lease_expires_at)
        lease = RunLease(
            thread_id=thread_id,
            run_id=run_id,
            owner_worker_id=owner_worker_id,
            lease_token=token,
            generation=generation,
            lease_expires_at=expires_at,
            lease_heartbeat_at=now_dt,
        )
        self._active_slots[thread_id] = lease
        run.update(
            {
                "status": "running",
                "owner_worker_id": owner_worker_id,
                "lease_token": token,
                "generation": generation,
                "lease_expires_at": expires_at.isoformat(),
                "lease_heartbeat_at": now_dt.isoformat(),
                "updated_at": now_dt.isoformat(),
            }
        )
        return lease

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
        now_dt = self._now(now)
        for thread_id, lease in tuple(self._active_slots.items()):
            if lease.run_id != run_id or lease.lease_token != lease_token or lease.generation != generation:
                continue
            if lease.lease_expires_at < now_dt:
                return False
            expires_at = self._lease_expires_at(now_dt, lease_expires_at)
            updated = RunLease(
                thread_id=thread_id,
                run_id=run_id,
                owner_worker_id=lease.owner_worker_id,
                lease_token=lease_token,
                generation=generation,
                lease_expires_at=expires_at,
                lease_heartbeat_at=now_dt,
            )
            self._active_slots[thread_id] = updated
            if run := self._runs.get(run_id):
                run["lease_expires_at"] = expires_at.isoformat()
                run["lease_heartbeat_at"] = now_dt.isoformat()
                if metadata_updates:
                    protected = {
                        "owner_worker_id",
                        "lease_token",
                        "generation",
                        "lease_expires_at",
                        "lease_heartbeat_at",
                    }
                    metadata = dict(run.get("metadata") or {})
                    metadata.update({key: value for key, value in metadata_updates.items() if key not in protected})
                    run["metadata"] = metadata
                run["updated_at"] = now_dt.isoformat()
            return True
        return False

    async def request_cancel(
        self,
        run_id: str,
        action: str,
        *,
        requested_by: str | None = None,
        now: datetime | None = None,
    ) -> CancelRequestResult | None:
        if action not in {"interrupt", "rollback"}:
            raise ValueError(f"unsupported cancel action: {action}")
        run = self._runs.get(run_id)
        if run is None:
            return None
        status = run.get("status")
        if is_terminal_status(status):
            return CancelRequestResult(
                run_id=run_id,
                status=status,
                action=run.get("cancel_action"),
                accepted=False,
                terminal=True,
                terminal_reason=self._terminal_reason(run),
            )
        now_iso = self._now(now).isoformat()
        current = run.get("cancel_action")
        cancel_action = "rollback" if action == "rollback" or current == "rollback" else "interrupt"
        run["cancellation_requested_at"] = run.get("cancellation_requested_at") or now_iso
        run["cancel_action"] = cancel_action
        if requested_by is not None:
            run["cancel_requested_by"] = requested_by
        if cancel_action == "rollback":
            run["rollback_requested_at"] = run.get("rollback_requested_at") or now_iso
        run["updated_at"] = now_iso
        return CancelRequestResult(run_id=run_id, status=status, action=cancel_action, accepted=True)

    async def consume_cancel_intent(
        self,
        run_id: str,
        *,
        lease_token: str,
        generation: int,
        now: datetime | None = None,
    ) -> CancelIntent | None:
        run = self._runs.get(run_id)
        if run is None:
            return None
        lease = self._active_slots.get(run["thread_id"])
        now_dt = self._now(now)
        if not is_active_status(run.get("status")) or lease is None or lease.run_id != run_id or lease.lease_token != lease_token or lease.generation != generation or lease.lease_expires_at < now_dt:
            return None
        action = run.get("cancel_action")
        requested_at = run.get("cancellation_requested_at")
        if action is None or requested_at is None:
            return None
        if action == "rollback":
            run["status"] = "rolling_back"
        elif run.get("status") != "rolling_back":
            run["status"] = "cancelling"
        run["updated_at"] = now_dt.isoformat()
        return CancelIntent(run_id=run_id, action=action, requested_at=requested_at, requested_by=run.get("cancel_requested_by"))

    def _matching_active_lease(
        self,
        run: dict[str, Any],
        lease_token: str,
        generation: int,
        *,
        now: datetime | None = None,
    ) -> RunLease | None:
        lease = self._active_slots.get(run["thread_id"])
        if lease is None:
            return None
        if lease.run_id != run["run_id"] or lease.lease_token != lease_token or lease.generation != generation:
            return None
        if lease.lease_expires_at < self._now(now):
            return None
        return lease

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
        run = self._runs.get(run_id)
        if run is None or run.get("status") not in set(from_statuses):
            return False
        if self._matching_active_lease(run, lease_token, generation, now=now) is None:
            return False
        run["status"] = to_status
        self._set_terminal_reason(run, terminal_reason)
        if error is not None:
            run["error"] = error
        run["updated_at"] = self._now(now).isoformat()
        return True

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
        run = self._runs.get(run_id)
        if run is None:
            return False
        if is_terminal_status(run.get("status")):
            return run.get("status") == terminal_status and run.get("lease_token") == lease_token and run.get("generation") == generation and self._terminal_reason(run) == terminal_reason
        if run.get("status") not in set(from_statuses):
            return False
        if self._matching_active_lease(run, lease_token, generation, now=now) is None:
            return False
        now_iso = self._now(now).isoformat()
        run["status"] = terminal_status
        self._set_terminal_reason(run, terminal_reason)
        run["completed_at"] = now_iso
        if error is not None:
            run["error"] = error
        for key, value in (completion_fields or {}).items():
            if value is not None:
                run[key] = value
        run["updated_at"] = now_iso
        self._active_slots.pop(run["thread_id"], None)
        return True

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
        run = self._runs.get(run_id)
        if run is None or run.get("status") != terminal_status or run.get("lease_token") != lease_token or run.get("generation") != generation or self._terminal_reason(run) != terminal_reason:
            return False
        completion_metadata = dict(metadata or {})
        run_metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        pending = run_metadata.pop("_pending_external_subagent_usage", None)
        if isinstance(pending, dict):
            for field, key in (("total_input_tokens", "input_tokens"), ("total_output_tokens", "output_tokens"), ("total_tokens", "total_tokens"), ("subagent_tokens", "total_tokens")):
                completion_metadata[field] = (completion_metadata.get(field) or 0) + (pending.get(key) or 0)
            usage_by_model = {name: dict(usage) for name, usage in (completion_metadata.get("token_usage_by_model") or {}).items()}
            for name, usage in (pending.get("by_model") or {}).items():
                bucket = usage_by_model.setdefault(name, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
                for key in bucket:
                    bucket[key] += usage.get(key, 0) or 0
            completion_metadata["token_usage_by_model"] = usage_by_model
        for key, value in completion_metadata.items():
            if key not in {"status", "terminal_reason"} and value is not None:
                run[key] = value
        run["updated_at"] = self._now(now).isoformat()
        return True

    async def release_active_slot(
        self,
        thread_id: str,
        run_id: str,
        *,
        lease_token: str,
        generation: int,
    ) -> bool:
        lease = self._active_slots.get(thread_id)
        if lease is None or lease.run_id != run_id or lease.lease_token != lease_token or lease.generation != generation:
            return False
        self._active_slots.pop(thread_id, None)
        return True

    async def list_expired_active_leases(self, now: datetime) -> list[RunLease]:
        now_dt = self._now(now)
        return [lease for lease in self._active_slots.values() if lease.lease_expires_at < now_dt]

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
        del recovery_worker_id, recovery_lease_token
        run = self._runs.get(run_id)
        if run is None:
            return False
        lease = self._active_slots.get(run["thread_id"])
        now_dt = self._now(now)
        if lease is None or lease.run_id != run_id or lease.generation != generation or lease.lease_expires_at >= now_dt:
            return False
        if not is_active_status(run.get("status")):
            return False
        reason = terminal_reason
        if reason is None:
            reason = "rollback_failed_owner_lost" if run.get("cancel_action") == "rollback" else "lease_expired_recovered"
        run["status"] = terminal_status
        self._set_terminal_reason(run, reason)
        run["completed_at"] = now_dt.isoformat()
        if error is not None:
            run["error"] = error
        run["updated_at"] = now_dt.isoformat()
        self._active_slots.pop(run["thread_id"], None)
        return True

    async def list_pending(self, *, before=None):
        now = before or datetime.now(UTC).isoformat()
        results = [r for r in self._runs.values() if r["status"] == "pending" and r["created_at"] <= now]
        results.sort(key=lambda r: r["created_at"])
        return results

    async def list_inflight(self, *, before=None):
        now = before or datetime.now(UTC).isoformat()
        results = [r for r in self._runs.values() if is_inflight_status(r["status"]) and r["created_at"] <= now]
        results.sort(key=lambda r: r["created_at"])
        return results

    async def aggregate_tokens_by_thread(
        self,
        thread_id: str,
        *,
        include_active: bool = False,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        statuses = ("success", "error", "running") if include_active else ("success", "error")
        # Use the thread index for an O(runs-in-thread) lookup instead of
        # scanning every run in the process (mirrors ``list_by_thread``).
        run_ids = self._runs_by_thread.get(thread_id) or ()
        completed = [run for run_id in run_ids if (run := self._runs.get(run_id)) is not None and run.get("status") in statuses and (user_id is None or run.get("user_id") == user_id)]
        by_model: dict[str, dict] = {}
        for r in completed:
            usage_by_model = r.get("token_usage_by_model") or {}
            if usage_by_model:
                for model, usage in usage_by_model.items():
                    entry = by_model.setdefault(model, {"tokens": 0, "runs": 0})
                    entry["tokens"] += usage.get("total_tokens", 0)
                    entry["runs"] += 1
            else:
                # Fallback for rows written before per-model accounting landed:
                # attribute the whole run to its single ``model_name``. Keeps
                # the legacy lead-only behavior for old data instead of
                # silently dropping it.
                model = r.get("model_name") or "unknown"
                entry = by_model.setdefault(model, {"tokens": 0, "runs": 0})
                entry["tokens"] += r.get("total_tokens", 0)
                entry["runs"] += 1
        return {
            "total_tokens": sum(r.get("total_tokens", 0) for r in completed),
            "total_input_tokens": sum(r.get("total_input_tokens", 0) for r in completed),
            "total_output_tokens": sum(r.get("total_output_tokens", 0) for r in completed),
            "total_runs": len(completed),
            "by_model": by_model,
            "by_caller": {
                "lead_agent": sum(r.get("lead_agent_tokens", 0) for r in completed),
                "subagent": sum(r.get("subagent_tokens", 0) for r in completed),
                "middleware": sum(r.get("middleware_tokens", 0) for r in completed),
            },
        }
