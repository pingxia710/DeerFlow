from __future__ import annotations

import asyncio
import json
import math
import statistics
import time
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from deerflow.persistence.round_state import MemoryRoundStateStore, RoundStateRepository
from deerflow.persistence.run import RunRepository
from deerflow.runtime.events.store.db import DbRunEventStore
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal
from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.schemas import RunStatus
from deerflow.runtime.runs.store.memory import MemoryRunStore
from deerflow.runtime.runs.worker import _publish_stream_frame
from deerflow.runtime.stream_bridge.memory import MemoryStreamBridge

_FAKE_IO_DELAY_SECONDS = 0.008
_CAPACITY_SAMPLE_COUNT = 5


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


class _SlowRunStore(MemoryRunStore):
    async def _slow(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(_FAKE_IO_DELAY_SECONDS)
        return await getattr(super(), method_name)(*args, **kwargs)

    async def list_expired_active_leases(self, *args: Any, **kwargs: Any) -> Any:
        return await self._slow("list_expired_active_leases", *args, **kwargs)

    async def list_inflight(self, *args: Any, **kwargs: Any) -> Any:
        return await self._slow("list_inflight", *args, **kwargs)

    async def create_pending_run(self, *args: Any, **kwargs: Any) -> Any:
        return await self._slow("create_pending_run", *args, **kwargs)

    async def try_acquire_active_slot(self, *args: Any, **kwargs: Any) -> Any:
        return await self._slow("try_acquire_active_slot", *args, **kwargs)

    async def heartbeat_lease(self, *args: Any, **kwargs: Any) -> Any:
        return await self._slow("heartbeat_lease", *args, **kwargs)

    async def cas_status(self, *args: Any, **kwargs: Any) -> Any:
        return await self._slow("cas_status", *args, **kwargs)

    async def complete_run(self, *args: Any, **kwargs: Any) -> Any:
        return await self._slow("complete_run", *args, **kwargs)


class _TerminalAckLossRunStore(_SlowRunStore):
    def __init__(self, ack_loss_threads: set[str]) -> None:
        super().__init__()
        self.ack_loss_threads = ack_loss_threads
        self.ack_loss_injections = 0
        self.ambiguous_runs: set[str] = set()
        self.confirmed_runs: set[str] = set()

    async def complete_run(self, run_id: str, **kwargs: Any) -> Any:
        result = await super().complete_run(run_id, **kwargs)
        thread_id = self._runs[run_id]["thread_id"]
        if thread_id in self.ack_loss_threads and run_id not in self.ambiguous_runs:
            self.ambiguous_runs.add(run_id)
            self.ack_loss_injections += 1
            raise RuntimeError("terminal commit acknowledgement lost")
        if run_id in self.ambiguous_runs and result:
            self.confirmed_runs.add(run_id)
        return result


class _SlowRoundStore(MemoryRoundStateStore):
    async def bind_run(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        await asyncio.sleep(_FAKE_IO_DELAY_SECONDS)
        return await super().bind_run(*args, **kwargs)

    async def record_task_events(self, *args: Any, **kwargs: Any) -> None:
        await asyncio.sleep(_FAKE_IO_DELAY_SECONDS)
        await super().record_task_events(*args, **kwargs)


class _SlowEventStore(MemoryRunEventStore):
    async def put(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        await asyncio.sleep(_FAKE_IO_DELAY_SECONDS)
        return await super().put(*args, **kwargs)

    async def put_batch(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        await asyncio.sleep(_FAKE_IO_DELAY_SECONDS)
        return await super().put_batch(*args, **kwargs)


class _SlowStreamBridge(MemoryStreamBridge):
    async def publish(self, *args: Any, **kwargs: Any) -> None:
        await asyncio.sleep(_FAKE_IO_DELAY_SECONDS)
        await super().publish(*args, **kwargs)

    async def publish_end(self, *args: Any, **kwargs: Any) -> None:
        await asyncio.sleep(_FAKE_IO_DELAY_SECONDS)
        await super().publish_end(*args, **kwargs)


async def _measure_event_loop_lag(stop: asyncio.Event, samples: list[float]) -> None:
    interval = 0.002
    loop = asyncio.get_running_loop()
    target = loop.time() + interval
    while not stop.is_set():
        await asyncio.sleep(interval)
        now = loop.time()
        samples.append(max(0.0, now - target))
        target = now + interval


async def _run_command_room(
    manager: RunManager,
    event_store: MemoryRunEventStore,
    round_store: MemoryRoundStateStore,
    bridge: MemoryStreamBridge,
    *,
    thread_id: str,
    intent: str | None = None,
    cancel: bool = False,
) -> tuple[Any, float]:
    started = time.perf_counter()
    record = await manager.create_or_reject(
        thread_id,
        assistant_id="command-room",
        kwargs={
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": intent or thread_id,
                    }
                ]
            }
        },
        user_id="owner-1",
    )
    assert await manager.set_status(record.run_id, RunStatus.running)

    journal = RunJournal(
        record.run_id,
        thread_id,
        event_store,
        round_store=round_store,
        round_id=record.round_id,
        user_id="owner-1",
        flush_threshold=100,
    )
    if cancel:
        assert await manager.cancel(record.run_id)
        task_event = {
            "type": "task_failed",
            "status": "failed",
            "task_id": "shared-task",
            "thread_id": thread_id,
            "run_id": record.run_id,
            "error_preview": "cancelled",
        }
        terminal_status = RunStatus.interrupted
        terminal_reason = "cancelled"
    else:
        task_event = {
            "type": "task_completed",
            "status": "completed",
            "task_id": "shared-task",
            "thread_id": thread_id,
            "run_id": record.run_id,
            "result_preview": intent or thread_id,
        }
        terminal_status = RunStatus.success
        terminal_reason = "success"
    journal.record_task_event(task_event)
    await journal.flush()
    await _publish_stream_frame(
        bridge,
        event_store,
        run_id=record.run_id,
        thread_id=thread_id,
        user_id="owner-1",
        event="values",
        data={"thread_id": thread_id, "run_id": record.run_id},
    )
    assert await manager.set_status(
        record.run_id,
        terminal_status,
        terminal_reason=terminal_reason,
    )
    await bridge.publish_end(record.run_id)
    return record, (time.perf_counter() - started) * 1000


async def _room_isolation_counts(
    manager: RunManager,
    event_store: MemoryRunEventStore,
    round_store: MemoryRoundStateStore,
    bridge: MemoryStreamBridge,
    records: list[Any],
) -> tuple[int, int, int]:
    consistency_errors = 0
    crosswire_count = 0
    lost_event_count = 0
    for record in records:
        events = await event_store.list_events(
            record.thread_id,
            record.run_id,
            user_id="owner-1",
        )
        lost_event_count += max(0, 2 - len(events))
        consistency_errors += max(0, len(events) - 2)
        crosswire_count += sum(event.get("thread_id") != record.thread_id or event.get("run_id") != record.run_id for event in events)
        round_rows = await round_store.list_by_thread(
            record.thread_id,
            user_id="owner-1",
        )
        matching_rounds = [row for row in round_rows if row.get("current_run_id") == record.run_id]
        if len(matching_rounds) != 1:
            consistency_errors += 1
        else:
            crosswire_count += matching_rounds[0].get("thread_id") != record.thread_id

        stream = bridge._streams.get(record.run_id)
        if stream is None:
            lost_event_count += 1
        else:
            lost_event_count += max(0, 1 - len(stream.events))
            consistency_errors += max(0, len(stream.events) - 1)
            crosswire_count += sum(not isinstance(entry.data, dict) or entry.data.get("thread_id") != record.thread_id or entry.data.get("run_id") != record.run_id for entry in stream.events)

        history = await manager.list_by_thread(
            record.thread_id,
            user_id="owner-1",
        )
        matching_runs = [row for row in history if row.run_id == record.run_id]
        if len(matching_runs) != 1:
            consistency_errors += 1
        else:
            consistency_errors += matching_runs[0].status != record.status

    for left in records:
        for right in records:
            if left is right or left.thread_id == right.thread_id:
                continue
            crosswire_count += len(
                await event_store.list_events(
                    left.thread_id,
                    right.run_id,
                    user_id="owner-1",
                )
            )
    return consistency_errors, crosswire_count, lost_event_count


async def _assert_room_isolation(
    manager: RunManager,
    event_store: MemoryRunEventStore,
    round_store: MemoryRoundStateStore,
    bridge: MemoryStreamBridge,
    records: list[Any],
) -> None:
    assert await _room_isolation_counts(
        manager,
        event_store,
        round_store,
        bridge,
        records,
    ) == (0, 0, 0)


async def _capacity_sample(
    room_count: int,
    sample_index: int,
) -> tuple[float, list[float], list[float]]:
    run_store = _SlowRunStore()
    round_store = _SlowRoundStore()
    event_store = _SlowEventStore()
    bridge = _SlowStreamBridge()
    manager = RunManager(
        store=run_store,
        round_store=round_store,
        terminal_cleanup_delay=-1,
    )
    lag_samples: list[float] = []
    stop_probe = asyncio.Event()
    probe_task = asyncio.create_task(_measure_event_loop_lag(stop_probe, lag_samples))
    started = time.perf_counter()
    results = await asyncio.gather(
        *[
            _run_command_room(
                manager,
                event_store,
                round_store,
                bridge,
                thread_id=f"capacity-{room_count}-{sample_index}-{room_index}",
            )
            for room_index in range(room_count)
        ],
        return_exceptions=True,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    stop_probe.set()
    await probe_task

    errors = [result for result in results if isinstance(result, BaseException)]
    assert errors == []
    successful_results = [result for result in results if not isinstance(result, BaseException)]
    records = [record for record, _duration_ms in successful_results]
    await _assert_room_isolation(
        manager,
        event_store,
        round_store,
        bridge,
        records,
    )
    return (
        elapsed_ms,
        [duration_ms for _record, duration_ms in successful_results],
        lag_samples,
    )


@pytest.mark.asyncio
async def test_command_room_control_plane_capacity_1_3_5_8() -> None:
    report: dict[str, Any] = {}
    for room_count in (1, 3, 5, 8):
        elapsed_samples: list[float] = []
        room_duration_samples: list[float] = []
        lag_samples: list[float] = []
        for sample_index in range(_CAPACITY_SAMPLE_COUNT):
            elapsed_ms, room_durations_ms, sample_lags = await _capacity_sample(
                room_count,
                sample_index,
            )
            elapsed_samples.append(elapsed_ms)
            room_duration_samples.extend(room_durations_ms)
            lag_samples.extend(sample_lags)

        report[str(room_count)] = {
            "samples": _CAPACITY_SAMPLE_COUNT,
            "errors": 0,
            "elapsed_mean_ms": round(statistics.mean(elapsed_samples), 3),
            "elapsed_p95_ms": round(_p95(elapsed_samples), 3),
            "elapsed_max_ms": round(max(elapsed_samples), 3),
            "room_mean_ms": round(statistics.mean(room_duration_samples), 3),
            "room_p95_ms": round(_p95(room_duration_samples), 3),
            "room_max_ms": round(max(room_duration_samples), 3),
            "event_loop_lag_p95_ms": round(_p95(lag_samples) * 1000, 3),
            "event_loop_lag_max_ms": round(max(lag_samples, default=0) * 1000, 3),
        }

    one_room_mean = report["1"]["elapsed_mean_ms"]
    five_room_mean = report["5"]["elapsed_mean_ms"]
    eight_room_mean = report["8"]["elapsed_mean_ms"]
    report["five_to_one_elapsed_ratio"] = round(
        five_room_mean / one_room_mean,
        3,
    )
    report["eight_to_one_elapsed_ratio"] = round(
        eight_room_mean / one_room_mean,
        3,
    )
    print("CAPACITY_BENCHMARK_JSON=" + json.dumps(report, sort_keys=True))

    assert five_room_mean < one_room_mean * 1.5
    assert eight_room_mean < one_room_mean * 1.5
    for room_count in (1, 3, 5, 8):
        metrics = report[str(room_count)]
        assert metrics["errors"] == 0
        assert metrics["event_loop_lag_p95_ms"] < 100
        assert metrics["elapsed_p95_ms"] <= metrics["room_p95_ms"] + 15


@pytest.mark.asyncio
async def test_eight_command_rooms_fault_mix_five_interleaved_waves() -> None:
    room_count = 8
    wave_count = 5
    cancelled_room_indexes = {1, 5}
    ack_loss_room_indexes = {2, 6}
    thread_ids = [f"mixed-capacity-thread-{index}" for index in range(room_count)]
    run_store = _TerminalAckLossRunStore({thread_ids[index] for index in ack_loss_room_indexes})
    round_store = _SlowRoundStore()
    event_store = _SlowEventStore()
    bridge = _SlowStreamBridge()
    manager = RunManager(
        store=run_store,
        round_store=round_store,
        terminal_cleanup_delay=-1,
    )
    wave_elapsed_samples: list[float] = []
    room_duration_samples: list[float] = []
    lag_samples: list[float] = []
    all_records: list[Any] = []
    unexpected_errors = 0
    stop_probe = asyncio.Event()
    probe_task = asyncio.create_task(_measure_event_loop_lag(stop_probe, lag_samples))

    for wave_index in range(wave_count):
        started = time.perf_counter()
        results = await asyncio.gather(
            *[
                _run_command_room(
                    manager,
                    event_store,
                    round_store,
                    bridge,
                    thread_id=thread_ids[room_index],
                    intent=f"wave-{wave_index}-room-{room_index}",
                    cancel=room_index in cancelled_room_indexes,
                )
                for room_index in range(room_count)
            ],
            return_exceptions=True,
        )
        wave_elapsed_samples.append((time.perf_counter() - started) * 1000)
        unexpected_errors += sum(isinstance(result, BaseException) for result in results)
        successful_results = [result for result in results if not isinstance(result, BaseException)]
        all_records.extend(record for record, _duration_ms in successful_results)
        room_duration_samples.extend(duration_ms for _record, duration_ms in successful_results)

    stop_probe.set()
    await probe_task
    consistency_errors, crosswire_count, lost_event_count = await _room_isolation_counts(
        manager,
        event_store,
        round_store,
        bridge,
        all_records,
    )
    for room_index, thread_id in enumerate(thread_ids):
        history = await manager.list_by_thread(thread_id, user_id="owner-1")
        rounds = await round_store.list_by_thread(thread_id, user_id="owner-1")
        consistency_errors += len(history) != wave_count
        consistency_errors += len(rounds) != wave_count
        expected_status = RunStatus.interrupted if room_index in cancelled_room_indexes else RunStatus.success
        consistency_errors += sum(record.status != expected_status for record in history)
        consistency_errors += sum("state" in row for row in rounds)

    consistency_errors += len(round_store.task_lanes) != room_count * wave_count
    cancelled_runs = sum(record.status == RunStatus.interrupted for record in all_records)
    report = {
        "rooms_per_wave": room_count,
        "waves": wave_count,
        "wave_elapsed_mean_ms": round(statistics.mean(wave_elapsed_samples), 3),
        "wave_elapsed_p95_ms": round(_p95(wave_elapsed_samples), 3),
        "wave_elapsed_max_ms": round(max(wave_elapsed_samples), 3),
        "room_duration_mean_ms": round(statistics.mean(room_duration_samples), 3),
        "room_duration_p95_ms": round(_p95(room_duration_samples), 3),
        "room_duration_max_ms": round(max(room_duration_samples), 3),
        "event_loop_lag_p95_ms": round(_p95(lag_samples) * 1000, 3),
        "event_loop_lag_max_ms": round(max(lag_samples, default=0) * 1000, 3),
        "errors": unexpected_errors + consistency_errors,
        "cross_thread_run_events": crosswire_count,
        "lost_events": lost_event_count,
        "cancelled_runs": cancelled_runs,
        "terminal_ack_loss_injected": run_store.ack_loss_injections,
        "terminal_ack_loss_recovered": len(run_store.confirmed_runs),
    }
    print("MIXED_CAPACITY_BENCHMARK_JSON=" + json.dumps(report, sort_keys=True))

    assert report["errors"] == 0
    assert report["cross_thread_run_events"] == 0
    assert report["lost_events"] == 0
    assert report["cancelled_runs"] == len(cancelled_room_indexes) * wave_count
    assert report["terminal_ack_loss_injected"] == len(ack_loss_room_indexes) * wave_count
    assert report["terminal_ack_loss_recovered"] == report["terminal_ack_loss_injected"]
    assert report["event_loop_lag_p95_ms"] < 100
    assert report["wave_elapsed_p95_ms"] <= report["room_duration_p95_ms"] * 1.5


@pytest.mark.asyncio
async def test_sqlite_eight_command_rooms_storage_boundary(
    tmp_path,
    caplog,
) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'command-room-capacity.db'}",
        connect_args={"timeout": 30},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    run_store = RunRepository(session_factory)
    round_store = RoundStateRepository(session_factory)
    event_store = DbRunEventStore(session_factory)
    bridge = MemoryStreamBridge()
    manager = RunManager(
        store=run_store,
        round_store=round_store,
        terminal_cleanup_delay=-1,
    )
    lag_samples: list[float] = []
    stop_probe = asyncio.Event()
    probe_task = asyncio.create_task(_measure_event_loop_lag(stop_probe, lag_samples))
    started = time.perf_counter()
    try:
        results = await asyncio.gather(
            *[
                _run_command_room(
                    manager,
                    event_store,
                    round_store,
                    bridge,
                    thread_id=f"sqlite-capacity-thread-{room_index}",
                )
                for room_index in range(8)
            ],
            return_exceptions=True,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        stop_probe.set()
        await probe_task
        unexpected_errors = [result for result in results if isinstance(result, BaseException)]
        successful_results = [result for result in results if not isinstance(result, BaseException)]
        records = [record for record, _duration_ms in successful_results]
        consistency_errors, crosswire_count, lost_event_count = await _room_isolation_counts(
            manager,
            event_store,
            round_store,
            bridge,
            records,
        )
        locked_count = 0
        for log_record in caplog.records:
            message = log_record.getMessage()
            if log_record.exc_info is not None:
                message += f" {log_record.exc_info[1]}"
            locked_count += "database is locked" in message.lower()
        room_durations = [duration_ms for _record, duration_ms in successful_results]
        report = {
            "rooms": 8,
            "waves": 1,
            "elapsed_ms": round(elapsed_ms, 3),
            "room_duration_mean_ms": round(
                statistics.mean(room_durations),
                3,
            )
            if room_durations
            else 0,
            "room_duration_p95_ms": round(_p95(room_durations), 3) if room_durations else 0,
            "room_duration_max_ms": round(max(room_durations), 3) if room_durations else 0,
            "event_loop_lag_p95_ms": round(_p95(lag_samples) * 1000, 3),
            "event_loop_lag_max_ms": round(
                max(lag_samples, default=0) * 1000,
                3,
            ),
            "errors": len(unexpected_errors) + consistency_errors,
            "database_locked": locked_count,
            "cross_thread_run_events": crosswire_count,
            "lost_events": lost_event_count,
            "error_messages": [str(error) for error in unexpected_errors],
        }
        print("SQLITE_CAPACITY_BENCHMARK_JSON=" + json.dumps(report, sort_keys=True))

        assert report["errors"] == 0, unexpected_errors
        assert report["database_locked"] == 0
        assert report["cross_thread_run_events"] == 0
        assert report["lost_events"] == 0
        assert report["event_loop_lag_p95_ms"] < 100
    finally:
        stop_probe.set()
        if not probe_task.done():
            await probe_task
        await engine.dispose()


@pytest.mark.asyncio
async def test_slow_command_room_does_not_block_unrelated_room() -> None:
    class GatedRunStore(_SlowRunStore):
        def __init__(self) -> None:
            super().__init__()
            self.slow_started = asyncio.Event()
            self.release_slow = asyncio.Event()

        async def create_pending_run(
            self,
            run_id: str,
            *,
            thread_id: str,
            **kwargs: Any,
        ) -> dict[str, Any]:
            if thread_id == "slow-thread":
                self.slow_started.set()
                await self.release_slow.wait()
            return await super().create_pending_run(
                run_id,
                thread_id=thread_id,
                **kwargs,
            )

    run_store = GatedRunStore()
    round_store = _SlowRoundStore()
    event_store = _SlowEventStore()
    bridge = _SlowStreamBridge()
    manager = RunManager(
        store=run_store,
        round_store=round_store,
        terminal_cleanup_delay=-1,
    )
    slow_task = asyncio.create_task(
        _run_command_room(
            manager,
            event_store,
            round_store,
            bridge,
            thread_id="slow-thread",
        )
    )
    await run_store.slow_started.wait()
    fast_task = asyncio.create_task(
        _run_command_room(
            manager,
            event_store,
            round_store,
            bridge,
            thread_id="fast-thread",
        )
    )
    try:
        fast_record, _duration_ms = await asyncio.wait_for(
            asyncio.shield(fast_task),
            timeout=1,
        )
        assert fast_record.status == RunStatus.success
        assert not slow_task.done()
    finally:
        run_store.release_slow.set()

    slow_record, _duration_ms = await slow_task
    await _assert_room_isolation(
        manager,
        event_store,
        round_store,
        bridge,
        [slow_record, fast_record],
    )


@pytest.mark.asyncio
async def test_single_conversation_keeps_three_sequential_rounds_consistent() -> None:
    run_store = MemoryRunStore()
    round_store = MemoryRoundStateStore()
    event_store = MemoryRunEventStore()
    bridge = MemoryStreamBridge()
    manager = RunManager(
        store=run_store,
        round_store=round_store,
        terminal_cleanup_delay=-1,
    )
    records = []
    for round_index in range(3):
        record, _duration_ms = await _run_command_room(
            manager,
            event_store,
            round_store,
            bridge,
            thread_id="sequential-thread",
            intent=f"round-{round_index}",
        )
        records.append(record)

    assert len({record.run_id for record in records}) == 3
    assert len({record.round_id for record in records}) == 3
    history = await manager.list_by_thread(
        "sequential-thread",
        user_id="owner-1",
    )
    assert {record.run_id for record in history} == {record.run_id for record in records}
    assert all(record.status == RunStatus.success for record in history)

    rounds = await round_store.list_by_thread(
        "sequential-thread",
        user_id="owner-1",
    )
    assert len(rounds) == 3
    assert {row["current_run_id"] for row in rounds} == {record.run_id for record in records}
    assert all("state" not in row for row in rounds)
    assert {(lane["thread_id"], lane["run_id"], lane["task_id"]) for lane in round_store.task_lanes.values()} == {("sequential-thread", record.run_id, "shared-task") for record in records}

    for record in records:
        events = await event_store.list_events(
            record.thread_id,
            record.run_id,
            user_id="owner-1",
        )
        assert len(events) == 2
        assert all(event["run_id"] == record.run_id for event in events)
