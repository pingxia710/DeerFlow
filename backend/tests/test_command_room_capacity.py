from __future__ import annotations

import asyncio
import json
import math
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TextIO

import pytest

import deerflow.command_room.round_record as round_record_module
from deerflow.command_room.account_ledger import build_account_update_proposal, record_account_update_proposal
from deerflow.command_room.handoff import HandoffEnvelope
from deerflow.command_room.pending_handoff import build_pending_handoff, record_pending_handoff
from deerflow.command_room.plan import build_round_plan, record_round_plan
from deerflow.command_room.quality import build_quality_signal, list_quality_signals, record_quality_signal
from deerflow.command_room.review import build_review_invocation, record_review_invocation
from deerflow.command_room.role_state import build_role_state, record_role_state
from deerflow.command_room.round_record import latest_command_room_round, record_command_room_round
from deerflow.subagents.audit import record_subagent_handoff


def _record_identity(row: dict[str, Any]) -> str:
    for key in ("signal_id", "state_id", "invocation_id", "handoff_id", "proposal_id", "plan_id", "task_id", "runId"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    raise AssertionError(f"Missing capacity-record identity: {sorted(row)}")


class _DelayedAppendFile:
    def __init__(self, file: TextIO, path: Path, tracker: _WriteTracker) -> None:
        self._file = file
        self._path = path
        self._tracker = tracker

    def __enter__(self) -> _DelayedAppendFile:
        self._file.__enter__()
        return self

    def __exit__(self, *args: object) -> bool | None:
        return self._file.__exit__(*args)

    def write(self, data: str) -> int:
        record_id = _record_identity(json.loads(data))
        started_at = time.perf_counter()
        with self._tracker.guard:
            self._tracker.active_total += 1
            self._tracker.active_by_path[self._path] = self._tracker.active_by_path.get(self._path, 0) + 1
            self._tracker.max_total = max(self._tracker.max_total, self._tracker.active_total)
            self._tracker.max_by_path[self._path] = max(
                self._tracker.max_by_path.get(self._path, 0),
                self._tracker.active_by_path[self._path],
            )
            self._tracker.write_order.setdefault(self._path, []).append(record_id)
            self._tracker.started_ids.add(record_id)
        try:
            if record_id in self._tracker.half_write_ids:
                midpoint = max(1, len(data) // 2)
                written = self._file.write(data[:midpoint])
                self._file.flush()
                with self._tracker.guard:
                    self._tracker.partial_ids.add(record_id)
                self._tracker.release_held.wait(timeout=2)
                return written + self._file.write(data[midpoint:])
            if record_id in self._tracker.held_ids:
                self._tracker.release_held.wait(timeout=2)
            else:
                time.sleep(self._tracker.delay_seconds)
            return self._file.write(data)
        finally:
            with self._tracker.guard:
                self._tracker.active_total -= 1
                self._tracker.active_by_path[self._path] -= 1
                self._tracker.completed_ids.add(record_id)
                self._tracker.write_durations.append(time.perf_counter() - started_at)


class _WriteTracker:
    def __init__(
        self,
        *,
        delay_seconds: float,
        held_ids: set[str] | None = None,
        half_write_ids: set[str] | None = None,
    ) -> None:
        self.delay_seconds = delay_seconds
        self.held_ids = held_ids or set()
        self.half_write_ids = half_write_ids or set()
        self.release_held = threading.Event()
        self.guard = threading.Lock()
        self.active_total = 0
        self.active_by_path: dict[Path, int] = {}
        self.max_total = 0
        self.max_by_path: dict[Path, int] = {}
        self.write_order: dict[Path, list[str]] = {}
        self.started_ids: set[str] = set()
        self.partial_ids: set[str] = set()
        self.completed_ids: set[str] = set()
        self.write_durations: list[float] = []


def _install_delayed_append(monkeypatch: Any, tracker: _WriteTracker, *, filenames: set[str] | None = None) -> None:
    real_open = Path.open
    instrumented = filenames or {"quality_signals.jsonl"}

    def delayed_open(path: Path, *args: Any, **kwargs: Any) -> TextIO | _DelayedAppendFile:
        file = real_open(path, *args, **kwargs)
        mode = args[0] if args else kwargs.get("mode", "r")
        if mode == "a" and path.name in instrumented:
            return _DelayedAppendFile(file, path, tracker)
        return file

    monkeypatch.setattr(Path, "open", delayed_open)


def _record_quality_signals_concurrently(*, count: int, base_dirs: list[Path]) -> float:
    barrier = threading.Barrier(count)

    def write(index: int) -> None:
        signal = build_quality_signal(
            signal_id=f"quality-{index}",
            thread_id=f"thread-{index if len(base_dirs) > 1 else 0}",
            run_id=f"run-{index}",
            author_role="evidence",
            recommendation="continue",
            rationale=f"capacity record {index}",
        )
        barrier.wait(timeout=2)
        record_quality_signal(signal, user_id=f"owner-{index}", base_dir=base_dirs[index % len(base_dirs)])

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=count) as executor:
        list(executor.map(write, range(count)))
    return time.perf_counter() - started


def _capacity_id(kind: str, room: int, wave: int) -> str:
    return f"{kind}-{room}-{wave}"


def _thread_id(room: int) -> str:
    return f"thread-{room}"


def _owner_id(room: int) -> str:
    return f"owner-{room}"


def _write_quality(room: int, wave: int, base_dir: Path) -> Path:
    signal = build_quality_signal(
        signal_id=_capacity_id("quality", room, wave),
        thread_id=_thread_id(room),
        run_id=_capacity_id("quality-run", room, wave),
        author_role="evidence",
        recommendation="continue",
        rationale=f"quality capacity wave {wave}",
    )
    return record_quality_signal(signal, user_id=_owner_id(room), base_dir=base_dir)


def _write_role_state(room: int, wave: int, base_dir: Path) -> Path:
    state = build_role_state(
        state_id=_capacity_id("role-state", room, wave),
        thread_id=_thread_id(room),
        role_name="evidence",
        summary=f"role capacity wave {wave}",
        run_id=_capacity_id("role-run", room, wave),
    )
    return record_role_state(state, user_id=_owner_id(room), base_dir=base_dir)


def _write_review(room: int, wave: int, base_dir: Path) -> Path:
    invocation = build_review_invocation(
        invocation_id=_capacity_id("review", room, wave),
        thread_id=_thread_id(room),
        run_id=_capacity_id("review-run", room, wave),
        requested_by_role="chair",
        reviewer_role="evidence_checker",
        reason=f"review capacity wave {wave}",
        focus="owner isolation",
    )
    return record_review_invocation(invocation, user_id=_owner_id(room), base_dir=base_dir)


def _write_pending_handoff(room: int, wave: int, base_dir: Path) -> Path:
    envelope = HandoffEnvelope(
        source_role="planner",
        target_role="evidence",
        task_or_question=f"handoff capacity wave {wave}",
        evidence_strength="Strong",
    )
    handoff = build_pending_handoff(
        handoff_id=_capacity_id("pending", room, wave),
        thread_id=_thread_id(room),
        run_id=_capacity_id("pending-run", room, wave),
        envelope=envelope,
    )
    return record_pending_handoff(handoff, user_id=_owner_id(room), base_dir=base_dir)


def _write_account(room: int, wave: int, base_dir: Path) -> Path:
    proposal = build_account_update_proposal(
        proposal_id=_capacity_id("account", room, wave),
        thread_id=_thread_id(room),
        run_id=_capacity_id("account-run", room, wave),
        proposed_by_role="recorder",
        account_type="evidence",
        proposed_change=f"account capacity wave {wave}",
        rationale="verify isolated append",
    )
    return record_account_update_proposal(proposal, user_id=_owner_id(room), base_dir=base_dir)


def _write_round_plan(room: int, wave: int, base_dir: Path) -> Path:
    plan = build_round_plan(
        plan_id=_capacity_id("round-plan", room, wave),
        thread_id=_thread_id(room),
        run_id=_capacity_id("plan-run", room, wave),
        goal=f"plan capacity wave {wave}",
    )
    return record_round_plan(plan, user_id=_owner_id(room), base_dir=base_dir)


def _write_command_round(room: int, wave: int, base_dir: Path) -> Path | None:
    return record_command_room_round(
        thread_id=_thread_id(room),
        agent_name="command-room",
        user_id=_owner_id(room),
        final_text="Verdict: NEEDS_MORE",
        run_id=_capacity_id("command-round", room, wave),
        source=f"capacity-wave-{wave}",
        audit_records=[],
        base_dir=base_dir,
    )


def _write_subagent_handoff(room: int, wave: int, base_dir: Path) -> Path | None:
    return record_subagent_handoff(
        thread_id=_thread_id(room),
        run_id=_capacity_id("subagent-run", room, wave),
        task_id=_capacity_id("subagent", room, wave),
        trace_id=_capacity_id("trace", room, wave),
        user_id=_owner_id(room),
        subagent_type="evidence",
        description=f"subagent capacity wave {wave}",
        prompt="Check isolated evidence and return a compact handoff.",
        status="completed",
        result="Role: evidence\nClaim: isolated\nEvidenceRefs: command output\nRedlineTouched: false\nNextAction: return",
        base_dir=base_dir,
    )


@dataclass(frozen=True)
class _RecordKind:
    name: str
    filename: str
    writer: Callable[[int, int, Path], Path | None]

    def record_id(self, room: int, wave: int) -> str:
        return _capacity_id(self.name, room, wave)


_QUALITY = _RecordKind("quality", "quality_signals.jsonl", _write_quality)
_ROLE_STATE = _RecordKind("role-state", "role_state.jsonl", _write_role_state)
_REVIEW = _RecordKind("review", "review_invocations.jsonl", _write_review)
_PENDING = _RecordKind("pending", "pending_handoffs.jsonl", _write_pending_handoff)
_ACCOUNT = _RecordKind("account", "account_ledger.jsonl", _write_account)
_ROUND_PLAN = _RecordKind("round-plan", "round_plans.jsonl", _write_round_plan)
_COMMAND_ROUND = _RecordKind("command-round", "command_room_rounds.jsonl", _write_command_round)
_SUBAGENT = _RecordKind("subagent", "subagent_handoffs.jsonl", _write_subagent_handoff)
_MIXED_WAVES = (
    (_QUALITY, _ROLE_STATE),
    (_REVIEW, _PENDING),
    (_ACCOUNT, _ROUND_PLAN),
    (_COMMAND_ROUND, _SUBAGENT),
    (_QUALITY, _SUBAGENT),
)


def _percentile_95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


async def _wait_for_record_ids(tracker: _WriteTracker, attribute: str, expected: set[str]) -> None:
    deadline = asyncio.get_running_loop().time() + 2
    while True:
        with tracker.guard:
            observed = set(getattr(tracker, attribute))
        if expected <= observed:
            return
        if asyncio.get_running_loop().time() >= deadline:
            pytest.fail(f"Timed out waiting for {attribute}: missing={sorted(expected - observed)}")
        await asyncio.sleep(0.001)


async def _monitor_loop_lag(stop: asyncio.Event, samples: list[float]) -> None:
    interval = 0.002
    expected = asyncio.get_running_loop().time() + interval
    while not stop.is_set():
        await asyncio.sleep(interval)
        now = asyncio.get_running_loop().time()
        samples.append(max(0.0, now - expected))
        expected = now + interval


async def _run_capacity_record(
    kind: _RecordKind,
    room: int,
    wave: int,
    base_dir: Path,
    operation_latencies: list[float],
    executor: ThreadPoolExecutor,
) -> Path:
    started = time.perf_counter()
    path = await asyncio.get_running_loop().run_in_executor(executor, kind.writer, room, wave, base_dir)
    operation_latencies.append(time.perf_counter() - started)
    assert path is not None
    return path


def test_distinct_command_rooms_write_in_parallel_at_1_3_5_capacity(tmp_path, monkeypatch) -> None:
    tracker = _WriteTracker(delay_seconds=0.06)
    _install_delayed_append(monkeypatch, tracker)

    elapsed: dict[int, float] = {}
    for count in (1, 3, 5):
        base_dirs = [tmp_path / f"capacity-{count}" / f"owner-{index}" / f"thread-{index}" for index in range(count)]
        elapsed[count] = _record_quality_signals_concurrently(count=count, base_dirs=base_dirs)

        for index, base_dir in enumerate(base_dirs):
            rows = list_quality_signals(
                thread_id=f"thread-{index}",
                user_id=f"owner-{index}",
                base_dir=base_dir,
            )
            assert [row["signal_id"] for row in rows] == [f"quality-{index}"]

    assert tracker.max_total >= 3
    assert elapsed[3] < elapsed[1] * 2.5
    assert elapsed[5] < elapsed[1] * 2.5


def test_same_command_room_keeps_single_writer_order_without_lost_records(tmp_path, monkeypatch) -> None:
    tracker = _WriteTracker(delay_seconds=0.02)
    _install_delayed_append(monkeypatch, tracker)
    base_dir = tmp_path / "owner-1" / "thread-1"

    _record_quality_signals_concurrently(count=5, base_dirs=[base_dir])

    path = base_dir / "quality_signals.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 5
    assert {row["signal_id"] for row in rows} == {f"quality-{index}" for index in range(5)}
    assert [row["signal_id"] for row in rows] == tracker.write_order[path]
    assert tracker.max_by_path[path] == 1


@pytest.mark.asyncio
async def test_same_file_reader_waits_for_complete_jsonl_append(tmp_path, monkeypatch) -> None:
    base_dir = tmp_path / "owner-0" / "thread-0" / "audit"
    partial_id = _capacity_id("quality", 0, 1)
    tracker = _WriteTracker(delay_seconds=0.001, half_write_ids={partial_id})
    _install_delayed_append(monkeypatch, tracker)

    await asyncio.to_thread(_write_quality, 0, 0, base_dir)
    writer = asyncio.create_task(asyncio.to_thread(_write_quality, 0, 1, base_dir))
    await _wait_for_record_ids(tracker, "partial_ids", {partial_id})
    reader = asyncio.create_task(
        asyncio.to_thread(
            list_quality_signals,
            thread_id=_thread_id(0),
            user_id=_owner_id(0),
            base_dir=base_dir,
        )
    )

    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(reader), timeout=0.05)
    finally:
        tracker.release_held.set()
        await writer

    rows = await reader
    assert [row["signal_id"] for row in rows] == [_capacity_id("quality", 0, 0), partial_id]


@pytest.mark.asyncio
async def test_latest_round_reader_never_observes_partial_json(tmp_path, monkeypatch) -> None:
    base_dir = tmp_path / "owner-0" / "thread-0" / "audit"
    partial_id = _capacity_id("command-round", 0, 1)
    tracker = _WriteTracker(delay_seconds=0.001, half_write_ids={partial_id})
    _install_delayed_append(monkeypatch, tracker, filenames={"command_room_rounds.jsonl"})

    await asyncio.to_thread(_write_command_round, 0, 0, base_dir)
    writer = asyncio.create_task(asyncio.to_thread(_write_command_round, 0, 1, base_dir))
    await _wait_for_record_ids(tracker, "partial_ids", {partial_id})
    reader = asyncio.create_task(
        asyncio.to_thread(
            latest_command_room_round,
            thread_id=_thread_id(0),
            user_id=_owner_id(0),
            base_dir=base_dir,
        )
    )

    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(reader), timeout=0.05)
    finally:
        tracker.release_held.set()
        await writer

    row = await reader
    assert row is not None
    assert row["runId"] == partial_id


@pytest.mark.asyncio
async def test_round_record_waits_for_complete_subagent_handoff_snapshot(tmp_path, monkeypatch) -> None:
    base_dir = tmp_path / "owner-0" / "thread-0" / "audit"
    partial_id = _capacity_id("subagent", 0, 1)
    tracker = _WriteTracker(delay_seconds=0.001, half_write_ids={partial_id})
    _install_delayed_append(monkeypatch, tracker, filenames={"subagent_handoffs.jsonl"})
    monkeypatch.setattr(
        round_record_module,
        "get_paths",
        lambda: SimpleNamespace(thread_dir=lambda *_args, **_kwargs: base_dir.parent),
    )

    await asyncio.to_thread(_write_subagent_handoff, 0, 0, base_dir)
    writer = asyncio.create_task(asyncio.to_thread(_write_subagent_handoff, 0, 1, base_dir))
    await _wait_for_record_ids(tracker, "partial_ids", {partial_id})
    reader = asyncio.create_task(
        asyncio.to_thread(
            record_command_room_round,
            thread_id=_thread_id(0),
            agent_name="command-room",
            user_id=_owner_id(0),
            final_text="Verdict: NEEDS_MORE",
            source="subagent-capacity-read",
            base_dir=base_dir,
        )
    )

    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(reader), timeout=0.05)
    finally:
        tracker.release_held.set()
        await writer

    path = await reader
    assert path is not None
    row = latest_command_room_round(thread_id=_thread_id(0), user_id=_owner_id(0), base_dir=base_dir)
    assert row is not None
    assert len(row["actionResults"]) == 2


@pytest.mark.asyncio
async def test_eight_command_rooms_keep_five_mixed_waves_isolated_and_parallel(tmp_path, monkeypatch) -> None:
    room_count = 8
    held_ids = {kind.record_id(room, len(_MIXED_WAVES) - 1) for room in range(room_count) for kind in _MIXED_WAVES[-1]}
    tracker = _WriteTracker(delay_seconds=0.02, held_ids=held_ids)
    filenames = {kind.filename for wave in _MIXED_WAVES for kind in wave}
    _install_delayed_append(monkeypatch, tracker, filenames=filenames)
    room_dirs = [tmp_path / _owner_id(room) / _thread_id(room) / "audit" for room in range(room_count)]
    operation_latencies: list[float] = []
    wave_durations: list[float] = []
    loop_lags: list[float] = []
    stop_monitor = asyncio.Event()
    monitor = asyncio.create_task(_monitor_loop_lag(stop_monitor, loop_lags))
    cancelled_writes = 0

    try:
        with ThreadPoolExecutor(max_workers=16) as executor:
            for wave_index, kinds in enumerate(_MIXED_WAVES):
                wave_started = time.perf_counter()
                metadata: list[tuple[int, _RecordKind, asyncio.Task[Path]]] = []
                for room in range(room_count):
                    for kind in kinds:
                        task = asyncio.create_task(
                            _run_capacity_record(
                                kind,
                                room,
                                wave_index,
                                room_dirs[room],
                                operation_latencies,
                                executor,
                            )
                        )
                        metadata.append((room, kind, task))

                concurrent_readers: list[asyncio.Task[list[dict[str, Any]]]] = []
                cancelled_keys: set[tuple[int, str]] = set()
                if wave_index == len(_MIXED_WAVES) - 1:
                    await _wait_for_record_ids(tracker, "started_ids", held_ids)
                    concurrent_readers = [
                        asyncio.create_task(
                            asyncio.to_thread(
                                list_quality_signals,
                                thread_id=_thread_id(room),
                                user_id=_owner_id(room),
                                base_dir=room_dirs[room],
                            )
                        )
                        for room in range(room_count)
                    ]
                    for room, kind, task in metadata:
                        if kind is _QUALITY and room % 2 == 1:
                            assert task.cancel()
                            cancelled_keys.add((room, kind.name))
                            cancelled_writes += 1
                    tracker.release_held.set()

                results = await asyncio.gather(*(task for _, _, task in metadata), return_exceptions=True)
                expected_ids = {kind.record_id(room, wave_index) for room in range(room_count) for kind in kinds}
                await _wait_for_record_ids(tracker, "completed_ids", expected_ids)
                for (room, kind, _), result in zip(metadata, results, strict=True):
                    if (room, kind.name) in cancelled_keys:
                        assert isinstance(result, asyncio.CancelledError)
                    else:
                        assert isinstance(result, Path)

                if concurrent_readers:
                    snapshots = await asyncio.gather(*concurrent_readers)
                    for room, rows in enumerate(snapshots):
                        assert [row["signal_id"] for row in rows] == [
                            _QUALITY.record_id(room, 0),
                            _QUALITY.record_id(room, wave_index),
                        ]
                        assert all(row["thread_id"] == _thread_id(room) for row in rows)

                wave_durations.append(time.perf_counter() - wave_started)
    finally:
        tracker.release_held.set()
        stop_monitor.set()
        await monitor

    kinds_by_filename = {kind.filename: kind for wave in _MIXED_WAVES for kind in wave}
    total_records = 0
    for room, base_dir in enumerate(room_dirs):
        assert {path.name for path in base_dir.glob("*.jsonl")} == filenames
        for filename, kind in kinds_by_filename.items():
            path = base_dir / filename
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            expected_ids = [candidate.record_id(room, wave_index) for wave_index, wave in enumerate(_MIXED_WAVES) for candidate in wave if candidate.filename == filename]
            assert [_record_identity(row) for row in rows] == expected_ids
            assert tracker.write_order[path] == expected_ids
            assert all((row.get("thread_id") or row.get("threadId")) == _thread_id(room) for row in rows)
            if kind is _SUBAGENT:
                assert all(row["user_id"] == _owner_id(room) for row in rows)
            total_records += len(rows)

    operation_p95 = _percentile_95(operation_latencies)
    wave_p95 = _percentile_95(wave_durations)
    loop_lag_p95 = _percentile_95(loop_lags)
    serial_per_type = room_count * tracker.delay_seconds
    assert total_records == room_count * sum(len(wave) for wave in _MIXED_WAVES)
    assert len(tracker.completed_ids) == total_records
    assert cancelled_writes == 4
    assert tracker.max_total >= room_count
    assert all(max_writers == 1 for max_writers in tracker.max_by_path.values())
    assert operation_p95 < serial_per_type * 0.75
    assert wave_p95 < serial_per_type * 0.75
    assert loop_lag_p95 < 0.1
    assert max(loop_lags) < 0.1
    print(
        "command_room_capacity_8 "
        f"records={total_records} cancelled={cancelled_writes} "
        f"operation_p95_ms={operation_p95 * 1000:.2f} "
        f"wave_p95_ms={wave_p95 * 1000:.2f} "
        f"loop_lag_p95_ms={loop_lag_p95 * 1000:.2f} "
        f"loop_lag_max_ms={max(loop_lags) * 1000:.2f}"
    )
