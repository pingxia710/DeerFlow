"""Gateway-owned background child execution and sequential Chair wakeups."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException
from starlette.requests import Request

from deerflow.runtime.background_tasks import CommandRoomBackgroundJob, CommandRoomBackgroundOutcome
from deerflow.runtime.runs.schemas import (
    CommandRoomWakeAdmission,
    CommandRoomWakeAdmissionUnavailable,
    CommandRoomWakeIdentityConflict,
    WakeAdmissionOutcome,
    WakeAdmissionResult,
    is_terminal_status,
    run_status_value,
)

logger = logging.getLogger(__name__)
_WAKE_RETRY_SECONDS = 0.25
_WAKE_STATUS_POLL_SECONDS = 0.25
_WAKE_MAX_ATTEMPTS = 3
_WAKE_LEASE_SECONDS = 30
_WAKE_LEASE_HEARTBEAT_SECONDS = 10
_BACKGROUND_STATE_KEY = "background_recovery"
_BACKGROUND_STATE_VERSION = 1
_OUTCOME_EVENT_TYPES = {
    "completed": "task_completed",
    "failed": "task_failed",
    "timed_out": "task_timed_out",
    "cancelled": "task_cancelled",
}
_TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "timed_out", "cancelled"})
_AMBIGUOUS_LEGACY_WAKE_ID = "ambiguous_legacy_wake_id"


@dataclass(frozen=True)
class _RequestSnapshot:
    app: Any
    headers: list[tuple[bytes, bytes]]
    state: dict[str, Any]

    @classmethod
    def from_request(cls, request: Request) -> _RequestSnapshot:
        return cls(
            app=request.app,
            headers=list(request.scope.get("headers", [])),
            state=dict(request.scope.get("state", {})),
        )

    def build_request(self, thread_id: str) -> Request:
        return Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": f"/api/threads/{thread_id}/runs",
                "raw_path": b"",
                "query_string": b"",
                "headers": list(self.headers),
                "client": None,
                "server": None,
                "app": self.app,
                "state": dict(self.state),
            }
        )

    @classmethod
    def for_recovery(cls, app: Any, user_id: str | None) -> _RequestSnapshot:
        state: dict[str, Any] = {}
        if user_id:
            state["user"] = SimpleNamespace(id=user_id)
        return cls(app=app, headers=[], state=state)


def _wake_message(
    job: CommandRoomBackgroundJob,
    outcome: CommandRoomBackgroundOutcome,
    *,
    task_lane_facts: str = "",
) -> str:
    result = outcome.result if outcome.result is not None else "(no child result)"
    error = outcome.error if outcome.error is not None else "(none)"
    return (
        "[Internal Command Room background completion]\n"
        "A one-shot child AI has reached a factual terminal state. This is an internal AI handoff, not a new human request.\n\n"
        f"source_run_id: {job.source_run_id}\n"
        f"task_id: {job.task_id}\n"
        f"role: {job.subagent_type}\n"
        f"description: {job.description}\n"
        f"status: {outcome.status}\n"
        f"error: {error}\n\n"
        f"Current sibling task facts:\n{task_lane_facts or '(unavailable)'}\n\n"
        "Complete child result:\n"
        f"{result}\n"
    )


async def _task_lane_facts(snapshot: _RequestSnapshot, job: CommandRoomBackgroundJob) -> str:
    state = getattr(getattr(snapshot, "app", None), "state", None)
    run_manager = getattr(state, "run_manager", None)
    round_store = getattr(state, "round_state_store", None)
    if run_manager is None or round_store is None or not hasattr(round_store, "list_task_lanes_by_round"):
        return ""
    try:
        source_run = await run_manager.get(job.source_run_id, user_id=None)
        round_id = getattr(source_run, "round_id", None)
        if not isinstance(round_id, str) or not round_id:
            return ""
        rows = await round_store.list_task_lanes_by_round(
            thread_id=job.thread_id,
            round_id=round_id,
            user_id=getattr(source_run, "user_id", None),
            limit=100,
        )
    except Exception:
        logger.warning("Could not load sibling task facts for task %s", job.task_id, exc_info=True)
        return ""
    facts = []
    for row in sorted(rows, key=lambda item: str(item.get("task_id") or "")):
        task_id = str(row.get("task_id") or "(unknown)")[:128]
        role = str(row.get("role") or row.get("subagent_type") or "(unknown)")[:128]
        status = str(row.get("status") or "(unknown)")[:64]
        facts.append(f"- {task_id} | {role} | {status}")
    return "\n".join(facts)


async def _start_wake_run(
    snapshot: _RequestSnapshot,
    job: CommandRoomBackgroundJob,
    outcome: CommandRoomBackgroundOutcome,
    *,
    wake_id: str | None = None,
    claim_id: str | None = None,
) -> Any:
    # Collect every awaitable input before the final lease fence.  The call to
    # start_run below then follows the successful conditional renewal directly.
    task_lane_facts = await _task_lane_facts(snapshot, job)
    if claim_id is not None and not await _renew_background_wake_claim(snapshot, job, claim_id):
        raise _WakeClaimLost
    return await _create_wake_run(snapshot, job, outcome, wake_id=wake_id, task_lane_facts=task_lane_facts)


class _WakeClaimLost(Exception):
    """The durable wake lease changed before the wake could be created."""


async def _create_wake_run(
    snapshot: _RequestSnapshot,
    job: CommandRoomBackgroundJob,
    outcome: CommandRoomBackgroundOutcome,
    *,
    wake_id: str | None = None,
    task_lane_facts: str,
) -> Any:
    # Lazy imports avoid a services -> deps -> background-service cycle.
    from app.gateway.routers.thread_runs import RunCreateRequest
    from app.gateway.services import start_run

    if wake_id is None:
        raise ValueError("command room wake creation requires a persisted wake_id")

    body = RunCreateRequest(
        assistant_id="command-room",
        input={
            "messages": [
                {
                    "role": "user",
                    "name": "command_room_background_result",
                    "content": _wake_message(job, outcome, task_lane_facts=task_lane_facts),
                    "additional_kwargs": {"hide_from_ui": True, "command_room_background_result": True},
                }
            ]
        },
        metadata={
            "command_room_wakeup": True,
            "source_run_id": job.source_run_id,
            "source_task_id": job.task_id,
            **({"command_room_wake_id": wake_id} if wake_id else {}),
            **({"round_id": job.round_id} if job.round_id else {}),
        },
        context=dict(job.wake_context),
        on_disconnect="continue",
        multitask_strategy="reject",
    )
    admission = CommandRoomWakeAdmission(
        wake_id=wake_id,
        thread_id=job.thread_id,
        user_id=CommandRoomBackgroundService._snapshot_user_id(snapshot),
        assistant_id="command-room",
        source_run_id=job.source_run_id,
        source_task_id=job.task_id,
        metadata=dict(body.metadata or {}),
        kwargs={
            "input": body.input,
            "config": body.config,
            "context": body.context,
            "command": body.command,
            "checkpoint_id": body.checkpoint_id,
            "checkpoint": body.checkpoint,
            "interrupt_before": body.interrupt_before,
            "interrupt_after": body.interrupt_after,
            "stream_mode": body.stream_mode,
            "stream_subgraphs": body.stream_subgraphs,
        },
        multitask_strategy=body.multitask_strategy,
        model_name=body.context.get("model_name") if isinstance(body.context, dict) else None,
    )
    return await start_run(
        body,
        job.thread_id,
        snapshot.build_request(job.thread_id),
        command_room_wake_admission=admission,
        return_command_room_wake_admission=True,
    )


async def _renew_background_wake_claim(
    snapshot: _RequestSnapshot,
    job: CommandRoomBackgroundJob,
    claim_id: str,
) -> bool:
    store = getattr(getattr(snapshot.app, "state", None), "round_state_store", None)
    renew = getattr(store, "renew_background_wake_claim", None)
    if not callable(renew):
        return False
    now = datetime.now(UTC)
    return await renew(
        thread_id=job.thread_id,
        run_id=job.source_run_id,
        task_id=job.task_id,
        user_id=CommandRoomBackgroundService._snapshot_user_id(snapshot),
        claim_id=claim_id,
        now=now,
        lease_expires_at=now + timedelta(seconds=_WAKE_LEASE_SECONDS),
    )


async def _wait_for_wake_run_terminal(snapshot: _RequestSnapshot, record: Any) -> str:
    state = getattr(getattr(snapshot, "app", None), "state", None)
    run_manager = getattr(state, "run_manager", None)
    run_id = getattr(record, "run_id", None)
    if run_manager is None or not isinstance(run_id, str) or not run_id:
        return "success"
    user_id = getattr(record, "user_id", None)
    while True:
        current = await run_manager.get(run_id, user_id=user_id, recover_stale=False)
        status = run_status_value(getattr(current, "status", None))
        if is_terminal_status(status):
            return status or "error"
        await asyncio.sleep(_WAKE_STATUS_POLL_SECONDS)


class BoundCommandRoomBackgroundDispatcher:
    """Owner-scoped dispatcher injected into one run's ToolRuntime."""

    def __init__(self, service: CommandRoomBackgroundService, snapshot: _RequestSnapshot):
        self._service = service
        self._snapshot = snapshot

    async def dispatch(self, job: CommandRoomBackgroundJob) -> None:
        await self._service.dispatch(job, self._snapshot)


class CommandRoomBackgroundService:
    """Keep child work and its Chair wake factually recoverable."""

    def __init__(self):
        self._tasks: dict[tuple[str, str, str], asyncio.Task[None]] = {}
        self._admission_lock = asyncio.Lock()
        self._closing = False

    def bind(self, request: Request) -> BoundCommandRoomBackgroundDispatcher:
        return BoundCommandRoomBackgroundDispatcher(self, _RequestSnapshot.from_request(request))

    @staticmethod
    def _task_key(job: CommandRoomBackgroundJob) -> tuple[str, str, str]:
        return (job.thread_id, job.source_run_id, job.task_id)

    async def dispatch(self, job: CommandRoomBackgroundJob, snapshot: _RequestSnapshot) -> None:
        if self._closing:
            raise RuntimeError("Command Room background service is shutting down")
        key = self._task_key(job)
        async with self._admission_lock:
            existing = self._tasks.get(key)
            if existing is not None and not existing.done():
                raise RuntimeError(f"Background task {job.task_id} is already running")
            lane = await self._get_lane(snapshot, job)
            background = self._background_from_lane(lane)
            if background is not None:
                raise RuntimeError(f"Background task {job.task_id} already has a durable admission")
            await self._persist_state(job, snapshot, outcome=None, wake={"state": "pending", "attempts": 0})
            task = asyncio.create_task(self._execute_and_wake(job, snapshot), name=f"command-room:{job.thread_id}:{job.task_id}")
            self._tasks[key] = task

            def discard(finished: asyncio.Task[None]) -> None:
                self._tasks.pop(key, None)
                if not finished.cancelled():
                    finished.exception()

            task.add_done_callback(discard)

    async def _execute_and_wake(self, job: CommandRoomBackgroundJob, snapshot: _RequestSnapshot) -> None:
        try:
            outcome = await job.execute()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Command Room background task %s failed outside its task contract", job.task_id)
            outcome = CommandRoomBackgroundOutcome(status="failed", error=f"Background task failed: {type(exc).__name__}")
        await self._persist_state(job, snapshot, outcome=outcome, wake={"state": "pending", "attempts": 0})
        claim_id = await self._claim_wake(job, snapshot)
        if claim_id is not None:
            await self._wake_with_claim(job, snapshot, outcome, claim_id)

    @staticmethod
    def _snapshot_user_id(snapshot: _RequestSnapshot) -> str | None:
        state = snapshot.state
        user = state.get("user")
        auth = state.get("auth")
        if user is None and auth is not None:
            user = getattr(auth, "user", None)
        user_id = getattr(user, "id", None)
        return str(user_id) if user_id else None

    @staticmethod
    def _state(snapshot: _RequestSnapshot) -> Any:
        return getattr(getattr(snapshot, "app", None), "state", None)

    @staticmethod
    def _background_from_lane(lane: Any) -> dict[str, Any] | None:
        handoff = lane.get("handoff") if isinstance(lane, dict) else None
        background = handoff.get(_BACKGROUND_STATE_KEY) if isinstance(handoff, dict) else None
        return dict(background) if isinstance(background, dict) else None

    async def _get_lane(self, snapshot: _RequestSnapshot, job: CommandRoomBackgroundJob) -> dict[str, Any] | None:
        store = getattr(self._state(snapshot), "round_state_store", None)
        get_lane = getattr(store, "get_task_lane", None)
        if not callable(get_lane):
            return None
        return await get_lane(
            thread_id=job.thread_id,
            run_id=job.source_run_id,
            task_id=job.task_id,
            user_id=self._snapshot_user_id(snapshot),
        )

    @staticmethod
    def _outcome_facts(outcome: CommandRoomBackgroundOutcome) -> dict[str, Any]:
        return {
            "status": outcome.status,
            "result": outcome.result,
            "error": outcome.error,
        }

    @staticmethod
    def _outcome_from_facts(value: Any) -> CommandRoomBackgroundOutcome | None:
        if not isinstance(value, dict):
            return None
        status = value.get("status")
        if status not in _OUTCOME_EVENT_TYPES:
            return None
        result = value.get("result")
        error = value.get("error")
        return CommandRoomBackgroundOutcome(
            status=status,
            result=result if isinstance(result, str) else None,
            error=error if isinstance(error, str) else None,
        )

    @staticmethod
    async def _unavailable_execute() -> CommandRoomBackgroundOutcome:
        raise RuntimeError("A Gateway restart cannot recover a Python background callable")

    async def _persist_state(
        self,
        job: CommandRoomBackgroundJob,
        snapshot: _RequestSnapshot,
        *,
        outcome: CommandRoomBackgroundOutcome | None,
        wake: dict[str, Any],
        claim_id: str | None = None,
    ) -> bool:
        state = self._state(snapshot)
        store = getattr(state, "round_state_store", None)
        record = getattr(store, "record_task_events", None)
        if not callable(record):
            raise RuntimeError("Command Room background admission requires round-state persistence")

        lane = await self._get_lane(snapshot, job)
        handoff = dict(lane.get("handoff") or {}) if isinstance(lane, dict) and isinstance(lane.get("handoff"), dict) else {}
        previous_background = self._background_from_lane(lane) or {}
        previous_outcome = self._outcome_from_facts(previous_background.get("outcome"))
        previous_wake = previous_background.get("wake") if isinstance(previous_background.get("wake"), dict) else None
        persisted_wake = previous_wake if previous_wake and previous_wake.get("state") in {"completed", "failed"} else dict(wake)
        background = {
            "version": _BACKGROUND_STATE_VERSION,
            "thread_id": job.thread_id,
            "source_run_id": job.source_run_id,
            "task_id": job.task_id,
            "description": job.description,
            "subagent_type": job.subagent_type,
            "round_id": job.round_id,
            "wake_context": dict(job.wake_context),
            "outcome": self._outcome_facts(previous_outcome or outcome) if previous_outcome or outcome else None,
            "wake": persisted_wake,
        }
        handoff[_BACKGROUND_STATE_KEY] = background
        event: dict[str, Any] = {
            "type": "command_room.background",
            "thread_id": job.thread_id,
            "run_id": job.source_run_id,
            "task_id": job.task_id,
            **({"round_id": job.round_id} if job.round_id else {}),
            "subagent_type": job.subagent_type,
            "description": job.description,
            "handoff_envelope": handoff,
        }
        if outcome is None and previous_outcome is None:
            event.update(type="task_started", status="in_progress")
        elif outcome is not None and previous_outcome is None:
            event.update(
                type=_OUTCOME_EVENT_TYPES[outcome.status],
                status=outcome.status,
                result_preview=outcome.result,
                error_preview=outcome.error,
            )
        if claim_id is not None:
            persist_claimed_wake = getattr(store, "persist_claimed_background_wake", None)
            if not callable(persist_claimed_wake):
                raise RuntimeError("Command Room background wake requires fenced state persistence")
            return await persist_claimed_wake(
                thread_id=job.thread_id,
                run_id=job.source_run_id,
                task_id=job.task_id,
                user_id=self._snapshot_user_id(snapshot),
                claim_id=claim_id,
                now=datetime.now(UTC),
                handoff=handoff,
                event=event if outcome is not None and previous_outcome is None else None,
            )
        await record([event])
        persisted = await self._get_lane(snapshot, job)
        if self._background_from_lane(persisted) is None:
            raise RuntimeError("Command Room background admission was not persisted")
        return True

    async def _claim_wake(self, job: CommandRoomBackgroundJob, snapshot: _RequestSnapshot) -> str | None:
        store = getattr(self._state(snapshot), "round_state_store", None)
        claim = getattr(store, "claim_background_wake", None)
        if not callable(claim):
            raise RuntimeError("Command Room background wake requires durable claim persistence")
        claim_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        claimed = await claim(
            thread_id=job.thread_id,
            run_id=job.source_run_id,
            task_id=job.task_id,
            user_id=self._snapshot_user_id(snapshot),
            claim_id=claim_id,
            now=now,
            lease_expires_at=now + timedelta(seconds=_WAKE_LEASE_SECONDS),
        )
        return claim_id if claimed else None

    async def _renew_wake_claim(self, job: CommandRoomBackgroundJob, snapshot: _RequestSnapshot, claim_id: str) -> bool:
        return await _renew_background_wake_claim(snapshot, job, claim_id)

    async def _release_wake_claim(self, job: CommandRoomBackgroundJob, snapshot: _RequestSnapshot, claim_id: str) -> None:
        store = getattr(self._state(snapshot), "round_state_store", None)
        release = getattr(store, "release_background_wake_claim", None)
        if not callable(release):
            return
        await release(
            thread_id=job.thread_id,
            run_id=job.source_run_id,
            task_id=job.task_id,
            user_id=self._snapshot_user_id(snapshot),
            claim_id=claim_id,
        )

    async def _keep_wake_claim(self, job: CommandRoomBackgroundJob, snapshot: _RequestSnapshot, claim_id: str, lost: asyncio.Event) -> None:
        while not self._closing:
            await asyncio.sleep(_WAKE_LEASE_HEARTBEAT_SECONDS)
            if not await self._renew_wake_claim(job, snapshot, claim_id):
                logger.warning("Command Room wake lease was lost for task %s", job.task_id)
                lost.set()
                return

    async def _wake_with_claim(
        self,
        job: CommandRoomBackgroundJob,
        snapshot: _RequestSnapshot,
        outcome: CommandRoomBackgroundOutcome,
        claim_id: str,
    ) -> None:
        lost = asyncio.Event()
        heartbeat = asyncio.create_task(self._keep_wake_claim(job, snapshot, claim_id, lost))
        try:
            await self._wake(job, snapshot, outcome, claim_id, lost)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            await self._release_wake_claim(job, snapshot, claim_id)

    async def _wake(
        self,
        job: CommandRoomBackgroundJob,
        snapshot: _RequestSnapshot,
        outcome: CommandRoomBackgroundOutcome,
        claim_id: str,
        lease_lost: asyncio.Event,
    ) -> None:
        lane = await self._get_lane(snapshot, job)
        background = self._background_from_lane(lane) or {}
        wake = background.get("wake") if isinstance(background.get("wake"), dict) else {}
        attempts = int(wake.get("attempts") or 0)
        while not self._closing and not lease_lost.is_set():
            if attempts >= _WAKE_MAX_ATTEMPTS:
                if not await self._persist_state(
                    job,
                    snapshot,
                    outcome=outcome,
                    wake={"state": "failed", "attempts": attempts, "last_status": wake.get("last_status"), "claim_id": claim_id},
                    claim_id=claim_id,
                ):
                    lease_lost.set()
                    return
                logger.error("Command Room wake failed after %s attempts for task %s", attempts, job.task_id)
                return
            wake_id = wake.get("wake_id") if isinstance(wake.get("wake_id"), str) and wake["wake_id"] else str(uuid.uuid4())
            if not await self._persist_state(
                job,
                snapshot,
                outcome=outcome,
                wake={"state": "starting", "attempts": attempts, "wake_id": wake_id, "claim_id": claim_id},
                claim_id=claim_id,
            ):
                lease_lost.set()
                return
            try:
                wake_start = await _start_wake_run(snapshot, job, outcome, wake_id=wake_id, claim_id=claim_id)
            except _WakeClaimLost:
                lease_lost.set()
                return
            except CommandRoomWakeIdentityConflict:
                if not await self._persist_state(
                    job,
                    snapshot,
                    outcome=outcome,
                    wake={
                        "state": "failed",
                        "attempts": attempts,
                        "wake_id": wake_id,
                        "last_status": "identity_conflict",
                        "claim_id": claim_id,
                    },
                    claim_id=claim_id,
                ):
                    lease_lost.set()
                return
            except CommandRoomWakeAdmissionUnavailable:
                if not await self._persist_state(
                    job,
                    snapshot,
                    outcome=outcome,
                    wake={
                        "state": "failed",
                        "attempts": attempts,
                        "wake_id": wake_id,
                        "last_status": "admission_unavailable",
                        "claim_id": claim_id,
                    },
                    claim_id=claim_id,
                ):
                    lease_lost.set()
                return
            except HTTPException as exc:
                if exc.status_code == 409:
                    wake = {"state": "pending", "attempts": attempts, "wake_id": wake_id, "claim_id": claim_id}
                    if not await self._persist_state(
                        job,
                        snapshot,
                        outcome=outcome,
                        wake=wake,
                        claim_id=claim_id,
                    ):
                        lease_lost.set()
                        return
                    await asyncio.sleep(_WAKE_RETRY_SECONDS)
                    continue
                logger.exception("Could not wake Command Room for task %s", job.task_id)
                attempts += 1
                wake = {"state": "pending", "attempts": attempts, "wake_id": wake_id, "last_status": f"http_{exc.status_code}", "claim_id": claim_id}
                if not await self._persist_state(job, snapshot, outcome=outcome, wake=wake, claim_id=claim_id):
                    lease_lost.set()
                    return
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Could not wake Command Room for task %s", job.task_id)
                attempts += 1
                wake = {"state": "pending", "attempts": attempts, "wake_id": wake_id, "last_status": type(exc).__name__, "claim_id": claim_id}
                if not await self._persist_state(job, snapshot, outcome=outcome, wake=wake, claim_id=claim_id):
                    lease_lost.set()
                    return
                continue
            if wake_start is None:
                if not await self._persist_state(
                    job,
                    snapshot,
                    outcome=outcome,
                    wake={"state": "completed", "attempts": attempts + 1, "wake_id": wake_id, "claim_id": claim_id},
                    claim_id=claim_id,
                ):
                    lease_lost.set()
                return
            wake_record = wake_start.record if isinstance(wake_start, WakeAdmissionResult) else wake_start
            wake_outcome = wake_start.outcome if isinstance(wake_start, WakeAdmissionResult) else WakeAdmissionOutcome.LEASE_WON
            if wake_outcome is WakeAdmissionOutcome.SUCCEEDED:
                if not await self._persist_state(
                    job,
                    snapshot,
                    outcome=outcome,
                    wake={"state": "completed", "attempts": attempts, "wake_id": wake_id, "run_id": getattr(wake_record, "run_id", None), "claim_id": claim_id},
                    claim_id=claim_id,
                ):
                    lease_lost.set()
                return
            if wake_outcome is WakeAdmissionOutcome.TERMINAL_FAILURE:
                attempts += 1
                wake = {"state": "pending", "attempts": attempts, "last_status": "terminal_failure", "claim_id": claim_id}
                if not await self._persist_state(job, snapshot, outcome=outcome, wake=wake, claim_id=claim_id):
                    lease_lost.set()
                    return
                await asyncio.sleep(_WAKE_RETRY_SECONDS)
                continue
            if wake_outcome is WakeAdmissionOutcome.ACTIVE_SLOT_BLOCKED:
                if not await self._persist_state(
                    job,
                    snapshot,
                    outcome=outcome,
                    wake={"state": "pending", "attempts": attempts, "wake_id": wake_id, "claim_id": claim_id},
                    claim_id=claim_id,
                ):
                    lease_lost.set()
                    return
                await asyncio.sleep(_WAKE_RETRY_SECONDS)
                continue
            if wake_outcome is WakeAdmissionOutcome.ADMISSION_UNAVAILABLE:
                if not await self._persist_state(
                    job,
                    snapshot,
                    outcome=outcome,
                    wake={
                        "state": "failed",
                        "attempts": attempts,
                        "wake_id": wake_id,
                        "last_status": "admission_unavailable",
                        "claim_id": claim_id,
                    },
                    claim_id=claim_id,
                ):
                    lease_lost.set()
                return
            if wake_record is None:
                raise RuntimeError("command room wake admission returned no canonical run")
            wake_run_id = getattr(wake_record, "run_id", None)
            if wake_outcome is WakeAdmissionOutcome.LEASE_WON:
                attempts += 1
                if not await self._persist_state(
                    job,
                    snapshot,
                    outcome=outcome,
                    wake={"state": "running", "attempts": attempts, "wake_id": wake_id, "run_id": wake_run_id, "claim_id": claim_id},
                    claim_id=claim_id,
                ):
                    lease_lost.set()
                    return
            try:
                wake_status = await _wait_for_wake_run_terminal(snapshot, wake_record)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Could not observe Command Room wake for task %s", job.task_id)
                wake_status = type(exc).__name__
            if lease_lost.is_set():
                return
            if wake_status == "success":
                if not await self._persist_state(
                    job,
                    snapshot,
                    outcome=outcome,
                    wake={"state": "completed", "attempts": attempts, "wake_id": wake_id, "run_id": wake_run_id, "claim_id": claim_id},
                    claim_id=claim_id,
                ):
                    lease_lost.set()
                return
            logger.warning("Retrying Command Room wake for task %s after wake run ended with %s", job.task_id, wake_status)
            attempts += 1 if wake_outcome is WakeAdmissionOutcome.ACTIVE else 0
            wake = {"state": "pending", "attempts": attempts, "last_status": wake_status, "claim_id": claim_id}
            if not await self._persist_state(job, snapshot, outcome=outcome, wake=wake, claim_id=claim_id):
                lease_lost.set()
                return
            await asyncio.sleep(_WAKE_RETRY_SECONDS)

    async def _recover_outcome(self, snapshot: _RequestSnapshot, job: CommandRoomBackgroundJob, lane: dict[str, Any]) -> CommandRoomBackgroundOutcome:
        event_store = getattr(self._state(snapshot), "run_event_store", None)
        list_events = getattr(event_store, "list_events", None)
        if callable(list_events):
            try:
                rows = await list_events(
                    job.thread_id,
                    job.source_run_id,
                    limit=500,
                    user_id=self._snapshot_user_id(snapshot),
                )
            except Exception:
                logger.warning("Could not load durable child outcome for task %s", job.task_id, exc_info=True)
            else:
                for row in reversed(rows):
                    content = row.get("content") if isinstance(row, dict) else None
                    if not isinstance(content, dict) or content.get("type") != "tool" or content.get("tool_call_id") != job.task_id:
                        continue
                    metadata = content.get("additional_kwargs")
                    status = metadata.get("subagent_status") if isinstance(metadata, dict) else None
                    if status in _OUTCOME_EVENT_TYPES:
                        return CommandRoomBackgroundOutcome(
                            status=status,
                            result=content.get("content") if isinstance(content.get("content"), str) else None,
                        )
        status = lane.get("status")
        if status in _OUTCOME_EVENT_TYPES:
            return CommandRoomBackgroundOutcome(
                status=status,
                result=lane.get("result") if isinstance(lane.get("result"), str) else None,
                error=lane.get("error") if isinstance(lane.get("error"), str) else None,
            )
        return CommandRoomBackgroundOutcome(
            status="failed",
            error="Gateway restarted before this background callable produced a durable outcome; it was not re-executed.",
        )

    async def _existing_wake_status(self, snapshot: _RequestSnapshot, job: CommandRoomBackgroundJob, wake: dict[str, Any]) -> str | None:
        state = self._state(snapshot)
        run_manager = getattr(state, "run_manager", None)
        if run_manager is None:
            return None
        run_id = wake.get("run_id")
        wake_id = wake.get("wake_id")
        if not isinstance(wake_id, str) or not wake_id:
            return _AMBIGUOUS_LEGACY_WAKE_ID if isinstance(run_id, str) and run_id else None
        find_wake = getattr(run_manager, "find_command_room_wake", None)
        if not callable(find_wake):
            return None
        record = await find_wake(
            CommandRoomWakeAdmission(
                wake_id=wake_id,
                thread_id=job.thread_id,
                user_id=self._snapshot_user_id(snapshot),
                assistant_id="command-room",
                source_run_id=job.source_run_id,
                source_task_id=job.task_id,
                metadata={},
                kwargs={},
            ),
            probe_stale=True,
        )
        if record is None:
            return _AMBIGUOUS_LEGACY_WAKE_ID if isinstance(run_id, str) and run_id else None
        return run_status_value(getattr(record, "status", None))

    async def recover(self, app: Any) -> None:
        """Resume pending wakes; running Python callables become factual failures."""
        state = getattr(app, "state", None)
        store = getattr(state, "round_state_store", None)
        list_background = getattr(store, "list_background_task_lanes", None)
        if not callable(list_background):
            logger.warning("Command Room background recovery is unavailable without durable round-state storage")
            return
        lanes = await list_background()
        for lane in lanes:
            background = self._background_from_lane(lane)
            if background is None:
                continue
            thread_id = lane.get("thread_id")
            run_id = lane.get("run_id")
            task_id = lane.get("task_id")
            if not all(isinstance(value, str) and value for value in (thread_id, run_id, task_id)):
                logger.warning("Skipping malformed Command Room background recovery record")
                continue
            if background.get("thread_id") != thread_id or background.get("source_run_id") != run_id or background.get("task_id") != task_id:
                logger.warning("Skipping mismatched Command Room background recovery record for task %s", task_id)
                continue
            job = CommandRoomBackgroundJob(
                thread_id=thread_id,
                source_run_id=run_id,
                task_id=task_id,
                description=str(background.get("description") or lane.get("description") or "Background task"),
                subagent_type=str(background.get("subagent_type") or lane.get("role") or "general-purpose"),
                execute=self._unavailable_execute,
                round_id=lane.get("round_id") if isinstance(lane.get("round_id"), str) else None,
                wake_context=dict(background.get("wake_context") or {}),
            )
            snapshot = _RequestSnapshot.for_recovery(app, lane.get("user_id") if isinstance(lane.get("user_id"), str) else None)
            wake = background.get("wake") if isinstance(background.get("wake"), dict) else {}
            if wake.get("state") == "completed" or wake.get("state") == "failed":
                continue
            key = self._task_key(job)
            if key in self._tasks and not self._tasks[key].done():
                continue
            claim_id = await self._claim_wake(job, snapshot)
            if claim_id is None:
                continue
            claimed = True
            try:
                lane = await self._get_lane(snapshot, job)
                background = self._background_from_lane(lane) or {}
                outcome = self._outcome_from_facts(background.get("outcome"))
                if outcome is None:
                    outcome = await self._recover_outcome(snapshot, job, lane or {})
                    if not await self._persist_state(
                        job,
                        snapshot,
                        outcome=outcome,
                        wake=dict(background.get("wake") or {"state": "pending", "attempts": 0}),
                        claim_id=claim_id,
                    ):
                        continue
                    lane = await self._get_lane(snapshot, job)
                    background = self._background_from_lane(lane) or {}
                wake = background.get("wake") if isinstance(background.get("wake"), dict) else {}
                if wake.get("state") == "completed" or wake.get("state") == "failed":
                    continue
                try:
                    wake_status = await self._existing_wake_status(snapshot, job, wake)
                except CommandRoomWakeIdentityConflict:
                    wake_status = "identity_conflict"
                except CommandRoomWakeAdmissionUnavailable:
                    wake_status = "admission_unavailable"
                if wake_status in {_AMBIGUOUS_LEGACY_WAKE_ID, "identity_conflict", "admission_unavailable"}:
                    if not await self._persist_state(
                        job,
                        snapshot,
                        outcome=outcome,
                        wake={
                            **wake,
                            "state": "failed",
                            "last_status": wake_status,
                            "claim_id": claim_id,
                        },
                        claim_id=claim_id,
                    ):
                        continue
                    continue
                if wake_status == "success":
                    if not await self._persist_state(
                        job,
                        snapshot,
                        outcome=outcome,
                        wake={**wake, "state": "completed", "claim_id": claim_id},
                        claim_id=claim_id,
                    ):
                        continue
                    continue
                if wake_status and not is_terminal_status(wake_status):
                    continue
                if wake_status:
                    wake = {"state": "pending", "attempts": int(wake.get("attempts") or 0), "last_status": wake_status, "claim_id": claim_id}
                    if not await self._persist_state(job, snapshot, outcome=outcome, wake=wake, claim_id=claim_id):
                        continue
                task = asyncio.create_task(
                    self._wake_with_claim(job, snapshot, outcome, claim_id),
                    name=f"command-room-recover:{job.thread_id}:{job.task_id}",
                )
                claimed = False
            finally:
                if claimed:
                    await self._release_wake_claim(job, snapshot, claim_id)
            self._tasks[key] = task

            def discard(finished: asyncio.Task[None], *, task_key: tuple[str, str, str] = key) -> None:
                self._tasks.pop(task_key, None)
                if not finished.cancelled():
                    finished.exception()

            task.add_done_callback(discard)

    async def shutdown(self) -> None:
        self._closing = True
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()


__all__ = ["BoundCommandRoomBackgroundDispatcher", "CommandRoomBackgroundService"]
