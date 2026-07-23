import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway import command_room_background as background_module
from deerflow.persistence.base import Base
from deerflow.persistence.round_state import MemoryRoundStateStore, RoundStateRepository
from deerflow.persistence.run import RunRepository
from deerflow.persistence.workspace_event import (
    RESULT_RECEIVED,
    MemoryWorkspaceEventStore,
)
from deerflow.runtime.background_tasks import CommandRoomBackgroundJob, CommandRoomBackgroundOutcome
from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.schemas import (
    CommandRoomWakeAdmissionUnavailable,
    CommandRoomWakeIdentityConflict,
    WakeAdmissionOutcome,
    WakeAdmissionResult,
)


async def _background_snapshot(*, user_id: str | None = None):
    round_store = MemoryRoundStateStore()
    await round_store.bind_run(thread_id="thread-1", run_id="run-1", user_id=user_id)
    app = SimpleNamespace(state=SimpleNamespace(round_state_store=round_store))
    state = {"user": SimpleNamespace(id=user_id)} if user_id else {}
    return background_module._RequestSnapshot(app=app, headers=[], state=state), round_store


def test_background_capacity_is_fifo_and_content_blind(monkeypatch):
    async def scenario():
        monkeypatch.setattr(background_module, "_MAX_EXECUTING_CHILDREN", 2)
        monkeypatch.setattr(background_module, "_MAX_QUEUED_CHILDREN", 2)
        monkeypatch.setattr(
            background_module,
            "_MAX_OUTSTANDING_CHILDREN_PER_COMMAND_ROOM",
            2,
        )
        round_store = MemoryRoundStateStore()
        app = SimpleNamespace(state=SimpleNamespace(round_state_store=round_store))

        async def snapshot(thread_id: str, run_id: str):
            await round_store.bind_run(thread_id=thread_id, run_id=run_id)
            return background_module._RequestSnapshot(app=app, headers=[], state={})

        release = asyncio.Event()
        two_running = asyncio.Event()
        started = 0

        async def execute():
            nonlocal started
            started += 1
            if started == 2:
                two_running.set()
            await release.wait()
            return CommandRoomBackgroundOutcome(status="completed", result="done")

        async def wake(*_args, **_kwargs):
            return None

        monkeypatch.setattr(background_module, "_start_wake_run", wake)
        service = background_module.CommandRoomBackgroundService()
        first_snapshot = await snapshot("thread-a", "run-a")
        second_snapshot = await snapshot("thread-b", "run-b")

        def job(thread_id: str, run_id: str, task_id: str):
            return CommandRoomBackgroundJob(
                thread_id=thread_id,
                source_run_id=run_id,
                task_id=task_id,
                description=task_id,
                subagent_type="executor",
                execute=execute,
            )

        await service.dispatch(job("thread-a", "run-a", "a-1"), first_snapshot)
        await service.dispatch(job("thread-b", "run-b", "b-1"), second_snapshot)
        await two_running.wait()
        await service.dispatch(job("thread-a", "run-a", "a-2"), first_snapshot)
        await service.dispatch(job("thread-b", "run-b", "b-2"), second_snapshot)

        queued_lane = await round_store.get_task_lane(
            thread_id="thread-a",
            run_id="run-a",
            task_id="a-2",
        )
        assert queued_lane["status"] == "pending"

        try:
            await service.dispatch(job("thread-c", "run-c", "c-1"), await snapshot("thread-c", "run-c"))
        except RuntimeError as exc:
            assert "queue is full" in str(exc)
        else:
            raise AssertionError("the global waiting queue should reject an admission after its numeric capacity")

        try:
            await service.dispatch(job("thread-a", "run-a", "a-3"), first_snapshot)
        except RuntimeError as exc:
            assert "2 queued or running" in str(exc)
        else:
            raise AssertionError("one Command Room should not hold more than its numeric capacity")

        release.set()
        await asyncio.gather(*tuple(service._tasks.values()))
        assert started == 4
        await service.shutdown()

    asyncio.run(scenario())


def test_background_waiting_jobs_run_in_fifo_order(monkeypatch):
    async def scenario():
        monkeypatch.setattr(background_module, "_MAX_EXECUTING_CHILDREN", 1)
        monkeypatch.setattr(background_module, "_MAX_QUEUED_CHILDREN", 2)
        monkeypatch.setattr(
            background_module,
            "_MAX_OUTSTANDING_CHILDREN_PER_COMMAND_ROOM",
            2,
        )
        round_store = MemoryRoundStateStore()
        app = SimpleNamespace(state=SimpleNamespace(round_state_store=round_store))

        async def snapshot(thread_id: str, run_id: str):
            await round_store.bind_run(thread_id=thread_id, run_id=run_id)
            return background_module._RequestSnapshot(app=app, headers=[], state={})

        release = asyncio.Event()
        first_running = asyncio.Event()
        started: list[str] = []

        async def execute(task_id: str):
            started.append(task_id)
            if task_id == "a-1":
                first_running.set()
                await release.wait()
            return CommandRoomBackgroundOutcome(status="completed", result=task_id)

        async def wake(*_args, **_kwargs):
            return None

        monkeypatch.setattr(background_module, "_start_wake_run", wake)
        service = background_module.CommandRoomBackgroundService()
        first_snapshot = await snapshot("thread-a", "run-a")
        second_snapshot = await snapshot("thread-b", "run-b")

        def job(thread_id: str, run_id: str, task_id: str):
            async def execute_job():
                return await execute(task_id)

            return CommandRoomBackgroundJob(
                thread_id=thread_id,
                source_run_id=run_id,
                task_id=task_id,
                description=task_id,
                subagent_type="executor",
                execute=execute_job,
            )

        await service.dispatch(job("thread-a", "run-a", "a-1"), first_snapshot)
        await first_running.wait()
        await service.dispatch(job("thread-a", "run-a", "a-2"), first_snapshot)
        await service.dispatch(job("thread-b", "run-b", "b-1"), second_snapshot)
        assert started == ["a-1"]

        release.set()
        await asyncio.gather(*tuple(service._tasks.values()))
        assert started == ["a-1", "a-2", "b-1"]
        await service.shutdown()

    asyncio.run(scenario())


def test_wake_status_wait_does_not_trigger_stale_recovery():
    async def scenario():
        calls: list[bool] = []

        class RunManager:
            async def get(self, _run_id, *, user_id=None, recover_stale=True):
                assert user_id == "user-1"
                calls.append(recover_stale)
                return SimpleNamespace(status="success")

        snapshot = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(run_manager=RunManager())))
        status = await background_module._wait_for_wake_run_terminal(
            snapshot,
            SimpleNamespace(run_id="wake-run", user_id="user-1"),
        )

        assert status == "success"
        assert calls == [False]

    asyncio.run(scenario())


def test_background_service_returns_immediately_and_retries_chair_wakeup(monkeypatch):
    async def scenario():
        service = background_module.CommandRoomBackgroundService()
        snapshot, _round_store = await _background_snapshot()
        release = asyncio.Event()
        wake_calls = []

        async def execute():
            await release.wait()
            return CommandRoomBackgroundOutcome(status="completed", result="verified child result")

        async def wake(_snapshot, job, outcome, **_kwargs):
            wake_calls.append((job.task_id, outcome.result))
            if len(wake_calls) == 1:
                raise HTTPException(status_code=409, detail="thread still active")

        monkeypatch.setattr(background_module, "_start_wake_run", wake)
        monkeypatch.setattr(background_module, "_WAKE_RETRY_SECONDS", 0)
        job = CommandRoomBackgroundJob(
            thread_id="thread-1",
            source_run_id="run-1",
            task_id="task-1",
            description="Execute",
            subagent_type="executor",
            execute=execute,
        )

        await service.dispatch(job, snapshot)
        assert len(service._tasks) == 1
        assert wake_calls == []

        release.set()
        await asyncio.gather(*tuple(service._tasks.values()))

        assert wake_calls == [
            ("task-1", "verified child result"),
            ("task-1", "verified child result"),
        ]
        await service.shutdown()

    asyncio.run(scenario())


def test_background_service_retries_when_an_admitted_wake_run_fails(monkeypatch):
    async def scenario():
        service = background_module.CommandRoomBackgroundService()
        snapshot, _round_store = await _background_snapshot()
        wake_calls = []
        terminal_statuses = ["error", "success"]

        async def execute():
            return CommandRoomBackgroundOutcome(status="completed", result="verified child result")

        async def wake(_snapshot, job, _outcome, **_kwargs):
            wake_calls.append(job.task_id)
            return SimpleNamespace(run_id=f"wake-{len(wake_calls)}")

        async def wait_for_terminal(_snapshot, _record):
            return terminal_statuses.pop(0)

        monkeypatch.setattr(background_module, "_start_wake_run", wake)
        monkeypatch.setattr(background_module, "_wait_for_wake_run_terminal", wait_for_terminal)
        monkeypatch.setattr(background_module, "_WAKE_RETRY_SECONDS", 0)
        job = CommandRoomBackgroundJob(
            thread_id="thread-1",
            source_run_id="run-1",
            task_id="task-1",
            description="Execute",
            subagent_type="executor",
            execute=execute,
        )

        await service.dispatch(job, snapshot)
        await asyncio.gather(*tuple(service._tasks.values()))

        assert wake_calls == ["task-1", "task-1"]
        await service.shutdown()

    asyncio.run(scenario())


def test_background_cancelled_child_is_delivered_and_wakes_chair(monkeypatch):
    async def scenario():
        service = background_module.CommandRoomBackgroundService()
        snapshot, round_store = await _background_snapshot(user_id="user-1")
        workspace_store = MemoryWorkspaceEventStore()
        snapshot.app.state.workspace_event_store = workspace_store
        wake_calls = []

        async def execute():
            raise asyncio.CancelledError

        async def wake(_snapshot, job, outcome, **_kwargs):
            wake_calls.append((job.task_id, outcome.status, outcome.error))

        monkeypatch.setattr(background_module, "_start_wake_run", wake)
        job = CommandRoomBackgroundJob(
            thread_id="thread-1",
            source_run_id="run-1",
            task_id="task-cancelled",
            description="Cancelled work",
            subagent_type="executor",
            execute=execute,
        )

        await service.dispatch(job, snapshot)
        await asyncio.gather(*tuple(service._tasks.values()))

        lane = await round_store.get_task_lane(
            thread_id="thread-1",
            run_id="run-1",
            task_id="task-cancelled",
            user_id="user-1",
        )
        inbox = await workspace_store.result_inbox(thread_id="thread-1", user_id="user-1")
        assert wake_calls == [("task-cancelled", "cancelled", "Background task cancelled")]
        assert lane["status"] == "cancelled"
        assert lane["handoff"]["background_recovery"]["wake"]["state"] == "completed"
        assert inbox["results"][0]["metadata"]["status"] == "cancelled"
        assert inbox["notified_through_seq"] == inbox["results"][0]["revision"]
        await service.shutdown()

    asyncio.run(scenario())


def test_concurrent_results_are_persisted_separately_and_wake_once(monkeypatch):
    async def scenario():
        class BarrierWorkspaceStore(MemoryWorkspaceEventStore):
            def __init__(self):
                super().__init__()
                self.result_appends = 0
                self.both_results_persisted = asyncio.Event()

            async def append(self, **kwargs):
                row = await super().append(**kwargs)
                if kwargs["event_type"] == RESULT_RECEIVED:
                    self.result_appends += 1
                    if self.result_appends == 2:
                        self.both_results_persisted.set()
                    await self.both_results_persisted.wait()
                return row

        service = background_module.CommandRoomBackgroundService()
        snapshot, round_store = await _background_snapshot(user_id="user-1")
        workspace_store = BarrierWorkspaceStore()
        snapshot.app.state.workspace_event_store = workspace_store
        wake_batches = []

        async def start_wake(_snapshot, _job, _outcome, **kwargs):
            wake_batches.append(kwargs["workspace_results"])
            return None

        monkeypatch.setattr(background_module, "_start_wake_run", start_wake)

        async def first_execute():
            return CommandRoomBackgroundOutcome(
                status="completed",
                result="Complete planner result, unchanged.",
            )

        async def second_execute():
            return CommandRoomBackgroundOutcome(
                status="completed",
                result="Complete opposition result, unchanged.",
            )

        jobs = [
            CommandRoomBackgroundJob(
                thread_id="thread-1",
                source_run_id="run-1",
                task_id="task-planner",
                description="Plan",
                subagent_type="planner",
                execute=first_execute,
            ),
            CommandRoomBackgroundJob(
                thread_id="thread-1",
                source_run_id="run-1",
                task_id="task-opposition",
                description="Challenge",
                subagent_type="opposition",
                execute=second_execute,
            ),
        ]
        for job in jobs:
            await service.dispatch(job, snapshot)
        await asyncio.gather(*tuple(service._tasks.values()))

        assert len(wake_batches) == 1
        assert [row["body"] for row in wake_batches[0]] == [
            "Complete planner result, unchanged.",
            "Complete opposition result, unchanged.",
        ]
        inbox = await workspace_store.result_inbox(
            thread_id="thread-1",
            user_id="user-1",
        )
        assert [row["body"] for row in inbox["results"]] == [
            "Complete planner result, unchanged.",
            "Complete opposition result, unchanged.",
        ]
        assert inbox["acknowledged_through_seq"] == 0
        assert inbox["notified_through_seq"] == max(row["revision"] for row in inbox["results"])

        lanes = [
            await round_store.get_task_lane(
                thread_id="thread-1",
                run_id="run-1",
                task_id=job.task_id,
                user_id="user-1",
            )
            for job in jobs
        ]
        wakes = [lane["handoff"]["background_recovery"]["wake"] for lane in lanes]
        assert all(wake["state"] == "completed" for wake in wakes)
        assert sorted(bool(wake.get("coalesced")) for wake in wakes) == [False, True]
        await service.shutdown()

    asyncio.run(scenario())


def test_recovery_records_notification_for_an_already_successful_inbox_wake(
    monkeypatch,
):
    async def scenario():
        service = background_module.CommandRoomBackgroundService()
        snapshot, round_store = await _background_snapshot(user_id="user-1")
        workspace_store = MemoryWorkspaceEventStore()
        snapshot.app.state.workspace_event_store = workspace_store
        wake_id = str(uuid4())

        class RunManager:
            async def find_command_room_wake(self, admission, *, probe_stale):
                assert admission.wake_id == wake_id
                assert probe_stale is True
                return SimpleNamespace(status="success")

        snapshot.app.state.run_manager = RunManager()

        async def execute():
            raise AssertionError("recovery must not execute persisted work")

        job = CommandRoomBackgroundJob(
            thread_id="thread-1",
            source_run_id="run-1",
            task_id="task-recovery-inbox",
            description="Research",
            subagent_type="fact-finder",
            execute=execute,
        )
        outcome = CommandRoomBackgroundOutcome(
            status="completed",
            result="Complete recovered result, unchanged.",
        )
        result_event = await service._ensure_result_event(
            snapshot,
            job,
            outcome,
        )
        assert result_event is not None
        await service._persist_state(
            job,
            snapshot,
            outcome=outcome,
            wake={
                "state": "running",
                "attempts": 1,
                "wake_id": wake_id,
                "run_id": "wake-run",
                "workspace_inbox_through_seq": result_event["revision"],
            },
            workspace_event_seq=result_event["revision"],
        )

        wake_calls = []

        async def start_wake(*_args, **_kwargs):
            wake_calls.append(True)

        monkeypatch.setattr(background_module, "_start_wake_run", start_wake)
        recovered_service = background_module.CommandRoomBackgroundService()
        await recovered_service.recover(snapshot.app)
        await asyncio.gather(*tuple(recovered_service._tasks.values()))

        inbox = await workspace_store.result_inbox(
            thread_id="thread-1",
            user_id="user-1",
        )
        lane = await round_store.get_task_lane(
            thread_id="thread-1",
            run_id="run-1",
            task_id="task-recovery-inbox",
            user_id="user-1",
        )
        assert wake_calls == []
        assert inbox["notified_through_seq"] == result_event["revision"]
        assert lane["handoff"]["background_recovery"]["wake"]["state"] == ("completed")
        assert recovered_service._tasks == {}
        await recovered_service.shutdown()

    asyncio.run(scenario())


def test_restart_marks_unrecoverable_callable_failed_and_wakes_once(monkeypatch):
    async def scenario():
        snapshot, round_store = await _background_snapshot()
        first_service = background_module.CommandRoomBackgroundService()
        calls = 0
        release = asyncio.Event()

        async def execute():
            nonlocal calls
            calls += 1
            await release.wait()
            return CommandRoomBackgroundOutcome(status="completed", result="must not survive restart")

        job = CommandRoomBackgroundJob(
            thread_id="thread-1",
            source_run_id="run-1",
            task_id="task-restart",
            description="Execution",
            subagent_type="executor",
            execute=execute,
        )
        await first_service.dispatch(job, snapshot)
        await asyncio.sleep(0)
        await first_service.shutdown()

        lane = await round_store.get_task_lane(thread_id="thread-1", run_id="run-1", task_id="task-restart")
        assert lane["handoff"]["background_recovery"]["outcome"] is None

        wakes = []

        async def wake(_snapshot, recovered_job, outcome, **_kwargs):
            wakes.append((recovered_job.task_id, outcome.status, outcome.error))

        monkeypatch.setattr(background_module, "_start_wake_run", wake)
        recovered_service = background_module.CommandRoomBackgroundService()
        await recovered_service.recover(snapshot.app)
        await asyncio.gather(*tuple(recovered_service._tasks.values()))

        recovered_lane = await round_store.get_task_lane(thread_id="thread-1", run_id="run-1", task_id="task-restart")
        background = recovered_lane["handoff"]["background_recovery"]
        assert calls == 1
        assert wakes == [("task-restart", "failed", "Gateway restarted before this background callable produced a durable outcome; it was not re-executed.")]
        assert recovered_lane["status"] == "failed"
        assert background["wake"]["state"] == "completed"
        await recovered_service.shutdown()

    asyncio.run(scenario())


def test_recovery_delivers_cancelled_child_and_wakes_chair(monkeypatch):
    async def scenario():
        snapshot, round_store = await _background_snapshot()
        service = background_module.CommandRoomBackgroundService()
        job = CommandRoomBackgroundJob(
            thread_id="thread-1",
            source_run_id="run-1",
            task_id="task-cancelled",
            description="Cancelled work",
            subagent_type="executor",
            execute=service._unavailable_execute,
        )
        await service._persist_state(
            job,
            snapshot,
            outcome=CommandRoomBackgroundOutcome(status="cancelled", error="stopped"),
            wake={"state": "pending", "attempts": 0},
        )

        wake_calls = []

        async def wake(_snapshot, recovered_job, recovered_outcome, **_kwargs):
            wake_calls.append((recovered_job.task_id, recovered_outcome.status))

        monkeypatch.setattr(background_module, "_start_wake_run", wake)
        await service.recover(snapshot.app)
        await asyncio.gather(*tuple(service._tasks.values()))

        lane = await round_store.get_task_lane(thread_id="thread-1", run_id="run-1", task_id="task-cancelled")
        wake_state = lane["handoff"]["background_recovery"]["wake"]
        assert wake_calls == [("task-cancelled", "cancelled")]
        assert wake_state["state"] == "completed"
        assert service._tasks == {}
        await service.shutdown()

    asyncio.run(scenario())


def test_recovery_does_not_retry_an_interrupted_chair_wake(monkeypatch):
    async def scenario():
        snapshot, round_store = await _background_snapshot()

        class RunManager:
            async def find_command_room_wake(self, *_args, **_kwargs):
                return SimpleNamespace(status="interrupted")

        snapshot.app.state.run_manager = RunManager()
        service = background_module.CommandRoomBackgroundService()
        job = CommandRoomBackgroundJob(
            thread_id="thread-1",
            source_run_id="run-1",
            task_id="task-interrupted-wake",
            description="Completed work",
            subagent_type="executor",
            execute=service._unavailable_execute,
        )
        await service._persist_state(
            job,
            snapshot,
            outcome=CommandRoomBackgroundOutcome(status="completed", result="done"),
            wake={
                "state": "running",
                "attempts": 1,
                "wake_id": str(uuid4()),
                "run_id": "stopped-wake-run",
            },
        )

        async def wake(*_args, **_kwargs):
            raise AssertionError("an interrupted Chair wake must not be retried")

        monkeypatch.setattr(background_module, "_start_wake_run", wake)
        await service.recover(snapshot.app)

        lane = await round_store.get_task_lane(thread_id="thread-1", run_id="run-1", task_id="task-interrupted-wake")
        wake_state = lane["handoff"]["background_recovery"]["wake"]
        assert wake_state["state"] == "failed"
        assert wake_state["last_status"] == "interrupted"
        assert service._tasks == {}
        await service.shutdown()

    asyncio.run(scenario())


def test_terminal_outcome_is_idempotent_and_recovery_does_not_execute_again(monkeypatch):
    async def scenario():
        snapshot, round_store = await _background_snapshot()
        service = background_module.CommandRoomBackgroundService()
        calls = 0

        async def execute():
            nonlocal calls
            calls += 1
            return CommandRoomBackgroundOutcome(status="completed", result="must not run")

        job = CommandRoomBackgroundJob(
            thread_id="thread-1",
            source_run_id="run-1",
            task_id="task-terminal",
            description="Inspection",
            subagent_type="general-purpose",
            execute=execute,
        )
        outcome = CommandRoomBackgroundOutcome(status="completed", result="durable child result")
        await service._persist_state(job, snapshot, outcome=outcome, wake={"state": "pending", "attempts": 0})
        await service._persist_state(job, snapshot, outcome=outcome, wake={"state": "pending", "attempts": 0})

        wakes = []

        async def wake(_snapshot, recovered_job, recovered_outcome, **_kwargs):
            wakes.append((recovered_job.task_id, recovered_outcome.result))

        monkeypatch.setattr(background_module, "_start_wake_run", wake)
        recovered_service = background_module.CommandRoomBackgroundService()
        await recovered_service.recover(snapshot.app)
        await asyncio.gather(*tuple(recovered_service._tasks.values()))
        await recovered_service.recover(snapshot.app)

        lane = await round_store.get_task_lane(thread_id="thread-1", run_id="run-1", task_id="task-terminal")
        assert calls == 0
        assert wakes == [("task-terminal", "durable child result")]
        assert lane["status"] == "completed"
        assert lane["handoff"]["background_recovery"]["wake"]["state"] == "completed"
        await recovered_service.shutdown()

    asyncio.run(scenario())


def test_recovery_retries_the_persisted_wake_id_before_lane_run_id_is_written(monkeypatch):
    async def scenario():
        snapshot, round_store = await _background_snapshot()
        wake_id = str(uuid4())

        async def execute():
            raise AssertionError("recovery must not execute persisted work")

        job = CommandRoomBackgroundJob(
            thread_id="thread-1",
            source_run_id="run-1",
            task_id="task-wake-created",
            description="Execution",
            subagent_type="executor",
            execute=execute,
        )
        outcome = CommandRoomBackgroundOutcome(status="completed", result="done")
        service = background_module.CommandRoomBackgroundService()
        await service._persist_state(
            job,
            snapshot,
            outcome=outcome,
            wake={"state": "starting", "attempts": 1, "wake_id": wake_id},
        )

        wake_calls = []

        async def start_wake(*_args, **kwargs):
            wake_calls.append(kwargs["wake_id"])
            return WakeAdmissionResult(
                record=SimpleNamespace(run_id="wake-run", status="success"),
                outcome=WakeAdmissionOutcome.SUCCEEDED,
                created=False,
            )

        monkeypatch.setattr(background_module, "_start_wake_run", start_wake)
        recovered_service = background_module.CommandRoomBackgroundService()
        await recovered_service.recover(snapshot.app)
        await asyncio.gather(*tuple(recovered_service._tasks.values()))

        lane = await round_store.get_task_lane(thread_id="thread-1", run_id="run-1", task_id="task-wake-created")
        assert wake_calls == [wake_id]
        assert lane["handoff"]["background_recovery"]["wake"]["state"] == "completed"
        assert recovered_service._tasks == {}
        await recovered_service.shutdown()

    asyncio.run(scenario())


def test_recovery_uses_exact_wake_lookup_and_fails_closed_for_legacy_cache(monkeypatch, tmp_path):
    async def scenario():
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'wake-exact-recovery.db'}"
        engine = create_async_engine(database_url)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        repository = RunRepository(async_sessionmaker(engine, expire_on_commit=False))
        manager = RunManager(repository)
        round_store = MemoryRoundStateStore()
        app = SimpleNamespace(state=SimpleNamespace(round_state_store=round_store, run_manager=manager))
        service = background_module.CommandRoomBackgroundService()
        outcome = CommandRoomBackgroundOutcome(status="completed", result="durable result")
        wake_id = str(uuid4())
        job = CommandRoomBackgroundJob(
            thread_id="thread-1",
            source_run_id="run-1",
            task_id="task-exact-recovery",
            description="Execution",
            subagent_type="executor",
            execute=service._unavailable_execute,
        )
        calls: list[str] = []

        async def start_wake(*_args, **kwargs):
            calls.append(kwargs["wake_id"])
            return WakeAdmissionResult(
                record=SimpleNamespace(run_id="unexpected", status="success"),
                outcome=WakeAdmissionOutcome.SUCCEEDED,
                created=False,
            )

        monkeypatch.setattr(background_module, "_start_wake_run", start_wake)
        try:
            await round_store.bind_run(thread_id="thread-1", run_id="run-1")
            admission = background_module.CommandRoomWakeAdmission(
                wake_id=wake_id,
                thread_id="thread-1",
                user_id=None,
                assistant_id="command-room",
                source_run_id="run-1",
                source_task_id="task-exact-recovery",
                metadata={},
                kwargs={},
            )
            canonical = await manager.create_or_reuse_command_room_wake(admission)
            assert canonical.record is not None
            await repository.update_status(canonical.record.run_id, "success")
            await service._persist_state(
                job,
                background_module._RequestSnapshot.for_recovery(app, None),
                outcome=outcome,
                wake={"state": "starting", "attempts": 1, "wake_id": wake_id},
            )

            await service.recover(app)
            lane = await round_store.get_task_lane(thread_id="thread-1", run_id="run-1", task_id="task-exact-recovery")
            assert calls == []
            assert lane["handoff"]["background_recovery"]["wake"]["state"] == "completed"

            legacy_wake_id = str(uuid4())
            await repository.put(
                "legacy-wake-run",
                thread_id="thread-1",
                assistant_id="command-room",
                status="success",
                metadata={
                    "command_room_wakeup": True,
                    "command_room_wake_id": legacy_wake_id,
                    "source_run_id": "run-1",
                    "source_task_id": "task-legacy-cache",
                },
            )
            legacy_job = CommandRoomBackgroundJob(
                thread_id="thread-1",
                source_run_id="run-1",
                task_id="task-legacy-cache",
                description="Execution",
                subagent_type="executor",
                execute=service._unavailable_execute,
            )
            await service._persist_state(
                legacy_job,
                background_module._RequestSnapshot.for_recovery(app, None),
                outcome=outcome,
                wake={"state": "starting", "attempts": 1, "wake_id": legacy_wake_id, "run_id": "legacy-wake-run"},
            )

            await service.recover(app)
            legacy_lane = await round_store.get_task_lane(thread_id="thread-1", run_id="run-1", task_id="task-legacy-cache")
            legacy_wake = legacy_lane["handoff"]["background_recovery"]["wake"]
            assert calls == []
            assert legacy_wake["state"] == "failed"
            assert legacy_wake["last_status"] == "ambiguous_legacy_wake_id"
        finally:
            await service.shutdown()
            await engine.dispose()

    asyncio.run(scenario())


def test_duplicate_background_receipt_is_rejected(monkeypatch):
    async def scenario():
        snapshot, _round_store = await _background_snapshot()
        service = background_module.CommandRoomBackgroundService()

        async def execute():
            return CommandRoomBackgroundOutcome(status="completed", result="done")

        async def wake(*_args, **_kwargs):
            return None

        monkeypatch.setattr(background_module, "_start_wake_run", wake)
        first = CommandRoomBackgroundJob(
            thread_id="thread-1",
            source_run_id="run-1",
            task_id="task-package",
            description="Execution",
            subagent_type="executor",
            execute=execute,
        )
        await service.dispatch(first, snapshot)
        await asyncio.gather(*tuple(service._tasks.values()))

        duplicate = CommandRoomBackgroundJob(
            thread_id="thread-1",
            source_run_id="run-1",
            task_id="task-package",
            description="Execution",
            subagent_type="executor",
            execute=execute,
        )
        try:
            await service.dispatch(duplicate, snapshot)
        except RuntimeError as exc:
            assert "already has a durable admission" in str(exc)
        else:
            raise AssertionError("duplicate background receipt was accepted")
        await service.shutdown()

    asyncio.run(scenario())


def test_task_lane_facts_include_sibling_statuses_for_each_wakeup():
    class RunManager:
        async def get(self, run_id, *, user_id=None):
            assert run_id == "run-1"
            assert user_id is None
            return SimpleNamespace(round_id="round-1", user_id="user-1")

    class RoundStore:
        async def list_task_lanes_by_round(self, **kwargs):
            assert kwargs == {
                "thread_id": "thread-1",
                "round_id": "round-1",
                "user_id": "user-1",
                "limit": 100,
            }
            return [
                {"task_id": "forward", "role": "technical-forward", "status": "completed"},
                {"task_id": "opposition", "role": "technical-opposition", "status": "in_progress"},
            ]

    snapshot = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                run_manager=RunManager(),
                round_state_store=RoundStore(),
            )
        )
    )
    job = CommandRoomBackgroundJob(
        thread_id="thread-1",
        source_run_id="run-1",
        task_id="opposition",
        description="Opposition",
        subagent_type="technical-opposition",
        execute=lambda: None,
    )

    facts = asyncio.run(background_module._task_lane_facts(snapshot, job))

    assert "forward | technical-forward | completed" in facts
    assert "opposition | technical-opposition | in_progress" in facts


def test_wake_message_marks_child_output_as_internal_factual_handoff():
    async def execute():
        return CommandRoomBackgroundOutcome(status="completed", result="done")

    job = CommandRoomBackgroundJob(
        thread_id="thread-1",
        source_run_id="run-1",
        task_id="task-1",
        description="Inspection",
        subagent_type="general-purpose",
        execute=execute,
    )

    message = background_module._wake_message(job, CommandRoomBackgroundOutcome(status="completed", result="inspection facts"))

    assert "internal AI handoff, not a new human request" in message
    assert "Compare it with the latest human conversation" not in message
    assert "Do not ask" not in message
    assert "inspection facts" in message


def test_result_inbox_wake_message_keeps_each_complete_result_separate():
    message = background_module._result_inbox_wake_message(
        [
            {
                "revision": 7,
                "body": "Complete first result.",
                "metadata": {"task_id": "task-1", "role": "planner"},
            },
            {
                "revision": 9,
                "body": "Complete second result.",
                "metadata": {"task_id": "task-2", "role": "opposition"},
            },
        ]
    )

    assert "inbox_through_seq: 9" in message
    assert "--- result_seq: 7 ---" in message
    assert "Complete first result." in message
    assert "--- result_seq: 9 ---" in message
    assert "Complete second result." in message


def test_start_wake_run_uses_hidden_input_and_command_room_context(monkeypatch):
    async def scenario():
        from app.gateway import services

        captured = {}

        async def start_run(body, thread_id, request, **kwargs):
            captured.update(body=body, thread_id=thread_id, request=request, **kwargs)

        class Snapshot:
            state = {}

            def build_request(self, thread_id):
                return {"thread_id": thread_id}

        async def execute():
            return CommandRoomBackgroundOutcome(status="completed", result="done")

        monkeypatch.setattr(services, "start_run", start_run)
        job = CommandRoomBackgroundJob(
            thread_id="thread-1",
            source_run_id="run-1",
            task_id="task-1",
            description="Execution",
            subagent_type="executor",
            execute=execute,
            wake_context={"model_name": "configured-model", "agent_name": "command-room"},
        )
        await background_module._start_wake_run(
            Snapshot(),
            job,
            CommandRoomBackgroundOutcome(status="completed", result="complete result"),
            wake_id=str(uuid4()),
        )

        body = captured["body"]
        message = body.input["messages"][0]
        assert captured["thread_id"] == "thread-1"
        assert body.assistant_id == "command-room"
        assert body.config is None
        config = services.build_run_config(
            captured["thread_id"],
            body.config,
            body.metadata,
            assistant_id=body.assistant_id,
        )
        assert config["recursion_limit"] == 1000
        assert body.context["model_name"] == "configured-model"
        assert captured["command_room_wake_admission"].wake_id == body.metadata["command_room_wake_id"]
        assert message["name"] == "command_room_background_result"
        assert message["additional_kwargs"]["hide_from_ui"] is True
        assert "complete result" in message["content"]

    asyncio.run(scenario())


def test_two_sqlite_gateways_reach_start_run_but_only_the_lease_winner_starts_a_worker(monkeypatch, tmp_path):
    async def scenario():
        from app.gateway import services

        database_url = f"sqlite+aiosqlite:///{tmp_path / 'two-gateway-real-start.db'}"
        first_engine = create_async_engine(database_url, connect_args={"timeout": 30})
        second_engine = create_async_engine(database_url, connect_args={"timeout": 30})
        async with first_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        first_manager = RunManager(RunRepository(async_sessionmaker(first_engine, expire_on_commit=False)), worker_id="first-gateway")
        second_manager = RunManager(RunRepository(async_sessionmaker(second_engine, expire_on_commit=False)), worker_id="second-gateway")
        thread_store = SimpleNamespace()

        async def get_thread(*_args, **_kwargs):
            return {"thread_id": "thread-1"}

        async def update_thread_status(*_args, **_kwargs):
            return None

        thread_store.get = get_thread
        thread_store.update_status = update_thread_status
        bridge = SimpleNamespace()
        worker_runs = []

        async def run_agent(_bridge, _manager, record, **_kwargs):
            worker_runs.append(record.run_id)
            record.status = "success"

        async def no_checkpoint(*_args, **_kwargs):
            return None

        monkeypatch.setattr(services, "get_stream_bridge", lambda _request: bridge)
        monkeypatch.setattr(services, "get_run_manager", lambda request: request.app.state.run_manager)
        monkeypatch.setattr(services, "get_run_context", lambda _request: SimpleNamespace(thread_store=thread_store, event_store=None))
        monkeypatch.setattr(services, "build_run_config", lambda *_args, **_kwargs: {"configurable": {}})
        monkeypatch.setattr(services, "owner_checkpoint_config", lambda *_args, **_kwargs: {"configurable": {}})
        monkeypatch.setattr(services, "apply_checkpoint_to_run_config", no_checkpoint)
        monkeypatch.setattr(services, "merge_run_context_overrides", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(services, "inject_authenticated_user_context", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(services, "normalize_input", lambda value: value)
        monkeypatch.setattr(services, "normalize_stream_modes", lambda _value: [])
        monkeypatch.setattr(services, "resolve_agent_factory", lambda _assistant_id: object())
        monkeypatch.setattr(services, "run_agent", run_agent)

        first_snapshot = background_module._RequestSnapshot(app=SimpleNamespace(state=SimpleNamespace(run_manager=first_manager)), headers=[], state={})
        second_snapshot = background_module._RequestSnapshot(app=SimpleNamespace(state=SimpleNamespace(run_manager=second_manager)), headers=[], state={})
        job = CommandRoomBackgroundJob(
            thread_id="thread-1",
            source_run_id="run-1",
            task_id="task-real-admission",
            description="Execution",
            subagent_type="executor",
            execute=lambda: None,
        )
        outcome = CommandRoomBackgroundOutcome(status="completed", result="durable result")
        wake_id = str(uuid4())
        try:
            first, second = await asyncio.gather(
                background_module._create_wake_run(first_snapshot, job, outcome, wake_id=wake_id, task_lane_facts="first"),
                background_module._create_wake_run(second_snapshot, job, outcome, wake_id=wake_id, task_lane_facts="second"),
            )
            assert isinstance(first, WakeAdmissionResult)
            assert isinstance(second, WakeAdmissionResult)
            assert first.record is not None and second.record is not None
            assert first.record.run_id == second.record.run_id
            assert sum(result.should_start_worker for result in (first, second)) == 1
            await asyncio.sleep(0)
            assert worker_runs == [first.record.run_id]
        finally:
            await first_engine.dispose()
            await second_engine.dispose()

    asyncio.run(scenario())


def test_wake_outcomes_keep_lane_writes_fenced_and_reuse_or_rotate_keys(monkeypatch):
    async def scenario():
        async def run_case(name, starts, *, expected_state, expected_status, expected_calls):
            snapshot, round_store = await _background_snapshot()
            service = background_module.CommandRoomBackgroundService()
            job = CommandRoomBackgroundJob(
                thread_id="thread-1",
                source_run_id="run-1",
                task_id=f"task-{name}",
                description="Execution",
                subagent_type="executor",
                execute=lambda: None,
            )
            outcome = CommandRoomBackgroundOutcome(status="completed", result="durable result")
            await service._persist_state(job, snapshot, outcome=outcome, wake={"state": "pending", "attempts": 0})
            calls = []
            persisted_states = []
            persist_state = service._persist_state

            async def record_persisted_state(*args, **kwargs):
                persisted_states.append(kwargs["wake"]["state"])
                return await persist_state(*args, **kwargs)

            async def start_wake(*_args, **kwargs):
                calls.append(kwargs["wake_id"])
                result = starts.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result

            monkeypatch.setattr(background_module, "_start_wake_run", start_wake)
            monkeypatch.setattr(background_module, "_WAKE_RETRY_SECONDS", 0)
            monkeypatch.setattr(background_module, "_wait_for_wake_run_terminal", lambda *_args: asyncio.sleep(0, result="success"))
            monkeypatch.setattr(service, "_persist_state", record_persisted_state)
            claim_id = await service._claim_wake(job, snapshot)
            assert claim_id is not None
            await service._wake_with_claim(job, snapshot, outcome, claim_id)
            lane = await round_store.get_task_lane(thread_id="thread-1", run_id="run-1", task_id=job.task_id)
            wake = lane["handoff"]["background_recovery"]["wake"]
            assert wake["state"] == expected_state
            assert wake.get("last_status") == expected_status
            assert len(calls) == expected_calls
            await service.shutdown()
            return calls, persisted_states

        active_calls, active_states = await run_case(
            "active",
            [
                WakeAdmissionResult(
                    record=SimpleNamespace(run_id="active-run", status="running"),
                    outcome=WakeAdmissionOutcome.ACTIVE,
                    created=False,
                )
            ],
            expected_state="completed",
            expected_status=None,
            expected_calls=1,
        )
        assert len(active_calls) == 1
        assert "running" not in active_states

        blocked_calls, _blocked_states = await run_case(
            "blocked",
            [
                HTTPException(status_code=409, detail="source active"),
                WakeAdmissionResult(
                    record=SimpleNamespace(run_id="blocked-run", status="running"),
                    outcome=WakeAdmissionOutcome.LEASE_WON,
                    created=False,
                ),
            ],
            expected_state="completed",
            expected_status=None,
            expected_calls=2,
        )
        assert blocked_calls[0] == blocked_calls[1]

        terminal_calls, _terminal_states = await run_case(
            "terminal",
            [
                WakeAdmissionResult(
                    record=SimpleNamespace(run_id="failed-run", status="error"),
                    outcome=WakeAdmissionOutcome.TERMINAL_FAILURE,
                    created=False,
                ),
                WakeAdmissionResult(
                    record=SimpleNamespace(run_id="replacement-run", status="running"),
                    outcome=WakeAdmissionOutcome.LEASE_WON,
                    created=True,
                ),
            ],
            expected_state="completed",
            expected_status=None,
            expected_calls=2,
        )
        assert terminal_calls[0] != terminal_calls[1]

        for name, error, status in (
            ("identity", CommandRoomWakeIdentityConflict("conflict"), "identity_conflict"),
            ("unavailable", CommandRoomWakeAdmissionUnavailable("unavailable"), "admission_unavailable"),
        ):
            calls, _states = await run_case(
                name,
                [error],
                expected_state="failed",
                expected_status=status,
                expected_calls=1,
            )
            assert len(calls) == 1

    asyncio.run(scenario())


def test_two_gateway_services_share_one_sqlite_wake_claim(monkeypatch, tmp_path):
    async def scenario():
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'two-gateway-wake.db'}"
        first_engine = create_async_engine(database_url, connect_args={"timeout": 30})
        second_engine = create_async_engine(database_url, connect_args={"timeout": 30})
        async with first_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        first_store = RoundStateRepository(async_sessionmaker(first_engine, expire_on_commit=False))
        second_store = RoundStateRepository(async_sessionmaker(second_engine, expire_on_commit=False))
        first_app = SimpleNamespace(state=SimpleNamespace(round_state_store=first_store))
        second_app = SimpleNamespace(state=SimpleNamespace(round_state_store=second_store))
        first_snapshot = background_module._RequestSnapshot(app=first_app, headers=[], state={})
        first_service = background_module.CommandRoomBackgroundService()
        second_service = background_module.CommandRoomBackgroundService()
        release_wake = asyncio.Event()
        wake_started = asyncio.Event()
        wake_calls = []

        async def execute():
            raise AssertionError("recovery must not execute the persisted child")

        async def start_wake(_snapshot, job, _outcome, **_kwargs):
            wake_calls.append(job.task_id)
            wake_started.set()
            await release_wake.wait()

        monkeypatch.setattr(background_module, "_start_wake_run", start_wake)
        job = CommandRoomBackgroundJob(
            thread_id="thread-1",
            source_run_id="run-1",
            task_id="task-shared-wake",
            description="Execution",
            subagent_type="executor",
            execute=execute,
        )
        outcome = CommandRoomBackgroundOutcome(status="completed", result="durable result")
        try:
            await first_store.bind_run(thread_id="thread-1", run_id="run-1")
            await first_service._persist_state(job, first_snapshot, outcome=outcome, wake={"state": "pending", "attempts": 0})

            await asyncio.gather(first_service.recover(first_app), second_service.recover(second_app))
            await asyncio.wait_for(wake_started.wait(), timeout=2)
            tasks = [*first_service._tasks.values(), *second_service._tasks.values()]

            assert len(tasks) == 1
            assert wake_calls == ["task-shared-wake"]
            release_wake.set()
            await asyncio.gather(*tasks)
            lane = await second_store.get_task_lane(thread_id="thread-1", run_id="run-1", task_id="task-shared-wake")
            assert lane["handoff"]["background_recovery"]["wake"]["state"] == "completed"
        finally:
            await first_service.shutdown()
            await second_service.shutdown()
            await first_engine.dispose()
            await second_engine.dispose()

    asyncio.run(scenario())


def test_expired_wake_claim_fences_the_old_gateway_before_wake_creation(monkeypatch, tmp_path):
    async def scenario():
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'fenced-expired-wake.db'}"
        first_engine = create_async_engine(database_url, connect_args={"timeout": 30})
        second_engine = create_async_engine(database_url, connect_args={"timeout": 30})
        async with first_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        first_store = RoundStateRepository(async_sessionmaker(first_engine, expire_on_commit=False))
        second_store = RoundStateRepository(async_sessionmaker(second_engine, expire_on_commit=False))
        first_app = SimpleNamespace(state=SimpleNamespace(round_state_store=first_store))
        second_app = SimpleNamespace(state=SimpleNamespace(round_state_store=second_store))
        first_snapshot = background_module._RequestSnapshot(app=first_app, headers=[], state={})
        second_snapshot = background_module._RequestSnapshot(app=second_app, headers=[], state={})
        first_service = background_module.CommandRoomBackgroundService()
        second_service = background_module.CommandRoomBackgroundService()
        first_claim_id = "first-gateway"
        second_claim_id = "second-gateway"
        first_fence_entered = asyncio.Event()
        release_first_fence = asyncio.Event()
        wake_calls = []

        async def execute():
            raise AssertionError("the durable child outcome must not run again")

        async def create_wake(_snapshot, _job, _outcome, *, wake_id=None, task_lane_facts):
            assert isinstance(task_lane_facts, str)
            wake_calls.append(wake_id)

        original_renew = background_module._renew_background_wake_claim

        async def renew(snapshot, job, claim_id):
            if claim_id == first_claim_id:
                first_fence_entered.set()
                await release_first_fence.wait()
            return await original_renew(snapshot, job, claim_id)

        monkeypatch.setattr(background_module, "_create_wake_run", create_wake)
        monkeypatch.setattr(background_module, "_renew_background_wake_claim", renew)
        job = CommandRoomBackgroundJob(
            thread_id="thread-1",
            source_run_id="run-1",
            task_id="task-fenced-expired-wake",
            description="Execution",
            subagent_type="executor",
            execute=execute,
        )
        outcome = CommandRoomBackgroundOutcome(status="completed", result="durable result")
        claimed_at = datetime.now(UTC)
        expires_at = claimed_at + timedelta(seconds=30)
        try:
            await first_store.bind_run(thread_id="thread-1", run_id="run-1")
            await first_service._persist_state(job, first_snapshot, outcome=outcome, wake={"state": "pending", "attempts": 0})
            assert await first_store.claim_background_wake(
                thread_id="thread-1",
                run_id="run-1",
                task_id="task-fenced-expired-wake",
                user_id=None,
                claim_id=first_claim_id,
                now=claimed_at,
                lease_expires_at=expires_at,
            )

            first_wake = asyncio.create_task(first_service._wake_with_claim(job, first_snapshot, outcome, first_claim_id))
            await asyncio.wait_for(first_fence_entered.wait(), timeout=2)
            first_lane = await first_store.get_task_lane(thread_id="thread-1", run_id="run-1", task_id="task-fenced-expired-wake")
            first_wake_id = first_lane["handoff"]["background_recovery"]["wake"]["wake_id"]

            takeover_at = expires_at + timedelta(seconds=1)
            assert await second_store.claim_background_wake(
                thread_id="thread-1",
                run_id="run-1",
                task_id="task-fenced-expired-wake",
                user_id=None,
                claim_id=second_claim_id,
                now=takeover_at,
                lease_expires_at=takeover_at + timedelta(seconds=30),
            )
            assert not await first_store.persist_claimed_background_wake(
                thread_id="thread-1",
                run_id="run-1",
                task_id="task-fenced-expired-wake",
                user_id=None,
                claim_id=first_claim_id,
                now=takeover_at,
                handoff={"background_recovery": {"wake": {"state": "stale"}}},
            )
            await second_service._wake_with_claim(job, second_snapshot, outcome, second_claim_id)

            release_first_fence.set()
            await first_wake

            lane = await second_store.get_task_lane(thread_id="thread-1", run_id="run-1", task_id="task-fenced-expired-wake")
            wake = lane["handoff"]["background_recovery"]["wake"]
            assert len(wake_calls) == 1
            assert wake_calls == [first_wake_id]
            assert wake["wake_id"] == first_wake_id
            assert wake["state"] == "completed"
            assert wake["claim_id"] == second_claim_id
        finally:
            release_first_fence.set()
            await first_service.shutdown()
            await second_service.shutdown()
            await first_engine.dispose()
            await second_engine.dispose()

    asyncio.run(scenario())


def test_takeover_during_real_task_lane_facts_fences_old_gateway_before_start_run(monkeypatch, tmp_path):
    async def scenario():
        from app.gateway import services

        database_url = f"sqlite+aiosqlite:///{tmp_path / 'fenced-task-lane-facts.db'}"
        first_engine = create_async_engine(database_url, connect_args={"timeout": 30})
        second_engine = create_async_engine(database_url, connect_args={"timeout": 30})
        async with first_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        first_store = RoundStateRepository(async_sessionmaker(first_engine, expire_on_commit=False))
        second_store = RoundStateRepository(async_sessionmaker(second_engine, expire_on_commit=False))
        facts_started = asyncio.Event()
        release_facts = asyncio.Event()
        start_calls = []

        class RunManager:
            def __init__(self, *, block: bool):
                self.block = block

            async def get(self, _run_id, *, user_id=None):
                assert user_id is None
                if self.block:
                    facts_started.set()
                    await release_facts.wait()
                return SimpleNamespace(round_id="round-1", user_id=None)

        first_app = SimpleNamespace(state=SimpleNamespace(round_state_store=first_store, run_manager=RunManager(block=True)))
        second_app = SimpleNamespace(state=SimpleNamespace(round_state_store=second_store, run_manager=RunManager(block=False)))
        first_snapshot = background_module._RequestSnapshot(app=first_app, headers=[], state={})
        second_snapshot = background_module._RequestSnapshot(app=second_app, headers=[], state={})

        async def execute():
            raise AssertionError("the durable child outcome must not run again")

        async def start_run(body, thread_id, request, **_kwargs):
            assert thread_id == "thread-1"
            assert request.scope["path"] == "/api/threads/thread-1/runs"
            start_calls.append(body.metadata["command_room_wake_id"])

        monkeypatch.setattr(services, "start_run", start_run)
        job = CommandRoomBackgroundJob(
            thread_id="thread-1",
            source_run_id="run-1",
            task_id="task-real-start-boundary",
            description="Execution",
            subagent_type="executor",
            execute=execute,
        )
        outcome = CommandRoomBackgroundOutcome(status="completed", result="durable result")
        wake_id = str(uuid4())
        first_claim_id = "first-gateway"
        second_claim_id = "second-gateway"
        claimed_at = datetime.now(UTC)
        expires_at = claimed_at + timedelta(seconds=30)
        try:
            await first_store.bind_run(thread_id="thread-1", run_id="run-1")
            await background_module.CommandRoomBackgroundService()._persist_state(
                job,
                first_snapshot,
                outcome=outcome,
                wake={"state": "starting", "attempts": 0, "wake_id": wake_id},
            )
            assert await first_store.claim_background_wake(
                thread_id="thread-1",
                run_id="run-1",
                task_id="task-real-start-boundary",
                user_id=None,
                claim_id=first_claim_id,
                now=claimed_at,
                lease_expires_at=expires_at,
            )

            old_start = asyncio.create_task(background_module._start_wake_run(first_snapshot, job, outcome, wake_id=wake_id, claim_id=first_claim_id))
            await asyncio.wait_for(facts_started.wait(), timeout=2)
            takeover_at = expires_at + timedelta(seconds=1)
            assert await second_store.claim_background_wake(
                thread_id="thread-1",
                run_id="run-1",
                task_id="task-real-start-boundary",
                user_id=None,
                claim_id=second_claim_id,
                now=takeover_at,
                lease_expires_at=takeover_at + timedelta(seconds=30),
            )

            release_facts.set()
            try:
                await old_start
            except background_module._WakeClaimLost:
                pass
            else:
                raise AssertionError("the old owner reached start_run after its claim was taken over")

            await background_module._start_wake_run(second_snapshot, job, outcome, wake_id=wake_id, claim_id=second_claim_id)

            assert start_calls == [wake_id]
        finally:
            release_facts.set()
            await first_engine.dispose()
            await second_engine.dispose()

    asyncio.run(scenario())


def test_recovery_missing_outcome_stale_owner_cannot_replace_winner_wake(tmp_path):
    async def scenario():
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'fenced-missing-outcome.db'}"
        first_engine = create_async_engine(database_url, connect_args={"timeout": 30})
        second_engine = create_async_engine(database_url, connect_args={"timeout": 30})
        async with first_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        first_store = RoundStateRepository(async_sessionmaker(first_engine, expire_on_commit=False))
        second_store = RoundStateRepository(async_sessionmaker(second_engine, expire_on_commit=False))
        outcome_read_started = asyncio.Event()
        release_outcome_read = asyncio.Event()

        class EventStore:
            async def list_events(self, *_args, **_kwargs):
                outcome_read_started.set()
                await release_outcome_read.wait()
                return []

        first_app = SimpleNamespace(state=SimpleNamespace(round_state_store=first_store, run_event_store=EventStore()))
        second_app = SimpleNamespace(state=SimpleNamespace(round_state_store=second_store))
        first_snapshot = background_module._RequestSnapshot(app=first_app, headers=[], state={})
        second_snapshot = background_module._RequestSnapshot(app=second_app, headers=[], state={})
        first_service = background_module.CommandRoomBackgroundService()
        second_service = background_module.CommandRoomBackgroundService()

        async def execute():
            raise AssertionError("recovery must not execute persisted work")

        job = CommandRoomBackgroundJob(
            thread_id="thread-1",
            source_run_id="run-1",
            task_id="task-fenced-missing-outcome",
            description="Execution",
            subagent_type="executor",
            execute=execute,
        )
        winner = CommandRoomBackgroundOutcome(status="completed", result="winner outcome")
        try:
            await first_store.bind_run(thread_id="thread-1", run_id="run-1")
            await first_service._persist_state(
                job,
                first_snapshot,
                outcome=None,
                wake={"state": "pending", "attempts": 0},
            )

            stale_recovery = asyncio.create_task(first_service.recover(first_app))
            await asyncio.wait_for(outcome_read_started.wait(), timeout=2)
            stale_lane = await second_store.get_task_lane(
                thread_id="thread-1",
                run_id="run-1",
                task_id="task-fenced-missing-outcome",
            )
            stale_expiry = stale_lane["wake_claim_expires_at"]
            assert await second_store.claim_background_wake(
                thread_id="thread-1",
                run_id="run-1",
                task_id="task-fenced-missing-outcome",
                user_id=None,
                claim_id="winner-gateway",
                now=stale_expiry + timedelta(seconds=1),
                lease_expires_at=stale_expiry + timedelta(seconds=31),
            )
            assert await second_service._persist_state(
                job,
                second_snapshot,
                outcome=winner,
                wake={"state": "starting", "attempts": 1, "wake_id": "winner-wake", "claim_id": "winner-gateway"},
                claim_id="winner-gateway",
            )

            release_outcome_read.set()
            await stale_recovery

            lane = await second_store.get_task_lane(
                thread_id="thread-1",
                run_id="run-1",
                task_id="task-fenced-missing-outcome",
            )
            background = lane["handoff"]["background_recovery"]
            assert lane["wake_claim_id"] == "winner-gateway"
            assert background["outcome"]["result"] == "winner outcome"
            assert background["wake"]["wake_id"] == "winner-wake"
            assert background["wake"]["state"] == "starting"
        finally:
            release_outcome_read.set()
            await first_service.shutdown()
            await second_service.shutdown()
            await first_engine.dispose()
            await second_engine.dispose()

    asyncio.run(scenario())


def test_expired_sqlite_wake_lease_is_recovered_by_a_new_gateway(monkeypatch, tmp_path):
    async def scenario():
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'expired-wake-lease.db'}"
        crashed_engine = create_async_engine(database_url)
        recovery_engine = create_async_engine(database_url)
        async with crashed_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        crashed_store = RoundStateRepository(async_sessionmaker(crashed_engine, expire_on_commit=False))
        recovery_store = RoundStateRepository(async_sessionmaker(recovery_engine, expire_on_commit=False))
        crashed_app = SimpleNamespace(state=SimpleNamespace(round_state_store=crashed_store))
        recovery_app = SimpleNamespace(state=SimpleNamespace(round_state_store=recovery_store))
        crashed_snapshot = background_module._RequestSnapshot(app=crashed_app, headers=[], state={})
        recovery_service = background_module.CommandRoomBackgroundService()
        recovered_wakes = []

        async def execute():
            raise AssertionError("recovery must not execute the persisted child")

        async def start_wake(_snapshot, job, _outcome, **_kwargs):
            recovered_wakes.append(job.task_id)

        monkeypatch.setattr(background_module, "_start_wake_run", start_wake)
        job = CommandRoomBackgroundJob(
            thread_id="thread-1",
            source_run_id="run-1",
            task_id="task-expired-wake",
            description="Execution",
            subagent_type="executor",
            execute=execute,
        )
        outcome = CommandRoomBackgroundOutcome(status="completed", result="durable result")
        now = datetime.now(UTC)
        try:
            await crashed_store.bind_run(thread_id="thread-1", run_id="run-1")
            await background_module.CommandRoomBackgroundService()._persist_state(
                job,
                crashed_snapshot,
                outcome=outcome,
                wake={"state": "pending", "attempts": 0},
            )
            assert await crashed_store.claim_background_wake(
                thread_id="thread-1",
                run_id="run-1",
                task_id="task-expired-wake",
                user_id=None,
                claim_id="crashed-gateway",
                now=now,
                lease_expires_at=now - timedelta(seconds=1),
            )

            await recovery_service.recover(recovery_app)
            tasks = tuple(recovery_service._tasks.values())
            assert len(tasks) == 1
            await asyncio.gather(*tasks)

            assert recovered_wakes == ["task-expired-wake"]
            lane = await recovery_store.get_task_lane(thread_id="thread-1", run_id="run-1", task_id="task-expired-wake")
            assert lane["handoff"]["background_recovery"]["wake"]["state"] == "completed"
        finally:
            await recovery_service.shutdown()
            await crashed_engine.dispose()
            await recovery_engine.dispose()

    asyncio.run(scenario())
