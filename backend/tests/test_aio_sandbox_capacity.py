import asyncio
import gc
import importlib
import math
import threading
import time
from dataclasses import dataclass

import pytest

from deerflow.config.paths import Paths


class _FakeSandboxClient:
    _counter_lock = threading.Lock()
    close_delay = 0.0
    active_closes = 0
    max_close_inflight = 0

    def __init__(self, id: str, base_url: str, api_key: str | None = None):
        self.id = id
        self.base_url = base_url
        self.api_key = api_key
        self.closed = False

    @classmethod
    def reset_close_metrics(cls, *, delay: float) -> None:
        with cls._counter_lock:
            cls.close_delay = delay
            cls.active_closes = 0
            cls.max_close_inflight = 0

    def close(self) -> None:
        cls = type(self)
        with cls._counter_lock:
            cls.active_closes += 1
            cls.max_close_inflight = max(
                cls.max_close_inflight,
                cls.active_closes,
            )
        try:
            time.sleep(cls.close_delay)
            self.closed = True
        finally:
            with cls._counter_lock:
                cls.active_closes -= 1


class _DelayedBackend:
    """Stateful fake backend that exposes real overlap and routing mistakes."""

    def __init__(self, sandbox_info_cls, *, delay: float):
        self._sandbox_info_cls = sandbox_info_cls
        self._delay = delay
        self._lock = threading.Lock()
        self._resources = {}
        self._active_total = 0
        self._active_by_sandbox: dict[str, int] = {}
        self.max_total_inflight = 0
        self.max_inflight_by_sandbox: dict[str, int] = {}
        self.created_by_sandbox: dict[str, str] = {}
        self.create_calls = 0
        self.create_calls_by_sandbox: dict[str, int] = {}
        self.create_started_sandboxes: set[str] = set()
        self.delay_by_sandbox: dict[str, float] = {}
        self.unknown_health_sandboxes: set[str] = set()
        self.destroy_failures_remaining: dict[str, int] = {}
        self.destroy_calls_by_sandbox: dict[str, int] = {}

    def _pause(self, sandbox_id: str) -> None:
        with self._lock:
            self._active_total += 1
            active = self._active_by_sandbox.get(sandbox_id, 0) + 1
            self._active_by_sandbox[sandbox_id] = active
            self.max_total_inflight = max(self.max_total_inflight, self._active_total)
            self.max_inflight_by_sandbox[sandbox_id] = max(
                self.max_inflight_by_sandbox.get(sandbox_id, 0),
                active,
            )
        try:
            time.sleep(self.delay_by_sandbox.get(sandbox_id, self._delay))
        finally:
            with self._lock:
                self._active_total -= 1
                self._active_by_sandbox[sandbox_id] -= 1

    def create(
        self,
        thread_id: str | None,
        sandbox_id: str,
        extra_mounts=None,
        *,
        user_id: str | None = None,
    ):
        del extra_mounts, user_id
        with self._lock:
            self.create_started_sandboxes.add(sandbox_id)
        self._pause(sandbox_id)
        info = self._sandbox_info_cls(
            sandbox_id=sandbox_id,
            sandbox_url=f"http://fake/{thread_id}/{sandbox_id}",
            sandbox_api_key=f"key-{sandbox_id}",
        )
        with self._lock:
            self._resources[sandbox_id] = info
            self.created_by_sandbox[sandbox_id] = thread_id or ""
            self.create_calls += 1
            self.create_calls_by_sandbox[sandbox_id] = self.create_calls_by_sandbox.get(sandbox_id, 0) + 1
        return info

    def discover(self, sandbox_id: str):
        self._pause(sandbox_id)
        with self._lock:
            return self._resources.get(sandbox_id)

    def is_alive(self, info) -> bool:
        self._pause(info.sandbox_id)
        with self._lock:
            if info.sandbox_id in self.unknown_health_sandboxes:
                raise RuntimeError(f"health unknown for {info.sandbox_id}")
            return self._resources.get(info.sandbox_id) is info

    def destroy(self, info) -> None:
        self._pause(info.sandbox_id)
        with self._lock:
            self.destroy_calls_by_sandbox[info.sandbox_id] = self.destroy_calls_by_sandbox.get(info.sandbox_id, 0) + 1
            failures = self.destroy_failures_remaining.get(info.sandbox_id, 0)
            if failures > 0:
                self.destroy_failures_remaining[info.sandbox_id] = failures - 1
                raise RuntimeError(f"destroy unknown for {info.sandbox_id}")
            self._resources.pop(info.sandbox_id, None)

    def list_running(self):
        with self._lock:
            return list(self._resources.values())

    def create_started(self, sandbox_ids: set[str]) -> bool:
        with self._lock:
            return sandbox_ids <= self.create_started_sandboxes

    def resource_ids(self) -> set[str]:
        with self._lock:
            return set(self._resources)


@dataclass(frozen=True)
class _CapacityResult:
    count: int
    cold_seconds: float
    release_seconds: float
    warm_seconds: float
    max_event_loop_lag_seconds: float
    error_count: int
    crossed_resource_count: int
    max_backend_inflight: int
    max_release_inflight: int

    @property
    def total_seconds(self) -> float:
        return self.cold_seconds + self.release_seconds + self.warm_seconds


@dataclass(frozen=True)
class _FaultWaveResult:
    wave: int
    wave_seconds: float
    operation_latencies: tuple[float, ...]
    normal_latencies: tuple[float, ...]
    slow_latencies: tuple[float, ...]
    loop_lags: tuple[float, ...]
    unexpected_errors: int
    crossed_resources: int
    leaked_resources: int
    cancellations: int
    recovered_destroy_failures: int
    max_backend_inflight: int
    max_close_inflight: int
    normal_max_seconds: float
    slow_backend_min_seconds: float


def _make_provider(aio_mod, backend):
    provider = aio_mod.AioSandboxProvider.__new__(aio_mod.AioSandboxProvider)
    provider._lock = threading.Lock()
    provider._sandboxes = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._thread_locks = aio_mod.WeakValueDictionary()
    provider._sandbox_locks = aio_mod.WeakValueDictionary()
    provider._last_activity = {}
    provider._warm_pool = {}
    provider._config = {"replicas": 10}
    provider._backend = backend
    provider._get_extra_mounts = lambda *_args, **_kwargs: []
    return provider


async def _heartbeat(stop: asyncio.Event, lags: list[float]) -> None:
    loop = asyncio.get_running_loop()
    interval = 0.005
    expected = loop.time() + interval
    while not stop.is_set():
        await asyncio.sleep(max(0.0, expected - loop.time()))
        now = loop.time()
        lags.append(max(0.0, now - expected))
        expected = now + interval


async def _ready(*_args, **_kwargs) -> bool:
    return True


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, math.ceil(len(ordered) * percentile) - 1)
    return ordered[index]


async def _timed_acquire(
    provider,
    thread_id: str,
    user_id: str,
    timings: dict[str, float],
) -> str:
    started = time.perf_counter()
    try:
        return await provider.acquire_async(thread_id, user_id=user_id)
    finally:
        timings[thread_id] = time.perf_counter() - started


async def _timed_thread_call(
    function,
    argument: str,
    timing_key: str,
    timings: dict[str, float],
):
    started = time.perf_counter()
    try:
        return await asyncio.to_thread(function, argument)
    finally:
        timings[timing_key] = time.perf_counter() - started


async def _wait_for_create_started(
    backend: _DelayedBackend,
    sandbox_ids: set[str],
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while not backend.create_started(sandbox_ids):
        if loop.time() >= deadline:
            raise AssertionError("cancel targets did not reach backend.create")
        await asyncio.sleep(0.001)


async def _run_capacity_cycle(aio_mod, count: int) -> _CapacityResult:
    backend = _DelayedBackend(aio_mod.SandboxInfo, delay=0.06)
    provider = _make_provider(aio_mod, backend)
    _FakeSandboxClient.reset_close_metrics(delay=0.06)
    user_id = "capacity-user"
    thread_ids = [f"capacity-{count}-room-{index}" for index in range(count)]
    errors: list[BaseException] = []
    lags: list[float] = []
    stop = asyncio.Event()
    heartbeat = asyncio.create_task(_heartbeat(stop, lags))
    await asyncio.sleep(0)

    try:
        started = time.perf_counter()
        cold_results = await asyncio.wait_for(
            asyncio.gather(
                *(provider.acquire_async(thread_id, user_id=user_id) for thread_id in thread_ids),
                return_exceptions=True,
            ),
            timeout=2,
        )
        cold_seconds = time.perf_counter() - started
        errors.extend(result for result in cold_results if isinstance(result, BaseException))
        cold_ids = [result for result in cold_results if isinstance(result, str)]

        started = time.perf_counter()
        release_results = await asyncio.wait_for(
            asyncio.gather(
                *(asyncio.to_thread(provider.release, sandbox_id) for sandbox_id in cold_ids),
                return_exceptions=True,
            ),
            timeout=2,
        )
        release_seconds = time.perf_counter() - started
        errors.extend(result for result in release_results if isinstance(result, BaseException))
        lifecycle_mismatches = int(set(provider._warm_pool) != set(cold_ids) or bool(provider._sandboxes) or bool(provider._thread_sandboxes))

        started = time.perf_counter()
        warm_results = await asyncio.wait_for(
            asyncio.gather(
                *(provider.acquire_async(thread_id, user_id=user_id) for thread_id in thread_ids),
                return_exceptions=True,
            ),
            timeout=2,
        )
        warm_seconds = time.perf_counter() - started
        errors.extend(result for result in warm_results if isinstance(result, BaseException))
        warm_ids = [result for result in warm_results if isinstance(result, str)]
    finally:
        stop.set()
        await heartbeat

    crossed = lifecycle_mismatches
    if len(cold_ids) != count or len(warm_ids) != count:
        crossed += abs(count - len(cold_ids)) + abs(count - len(warm_ids))
    if cold_ids != warm_ids or len(set(warm_ids)) != count:
        crossed += 1
    if provider._warm_pool or set(provider._sandboxes) != set(warm_ids):
        crossed += 1

    for thread_id, sandbox_id in zip(thread_ids, warm_ids, strict=False):
        expected_id = provider._deterministic_sandbox_id(thread_id, user_id)
        sandbox = provider.get(sandbox_id)
        if (
            sandbox_id != expected_id
            or backend.created_by_sandbox.get(sandbox_id) != thread_id
            or sandbox is None
            or sandbox.base_url != f"http://fake/{thread_id}/{sandbox_id}"
            or provider._thread_sandboxes.get((user_id, thread_id)) != sandbox_id
        ):
            crossed += 1

    return _CapacityResult(
        count=count,
        cold_seconds=cold_seconds,
        release_seconds=release_seconds,
        warm_seconds=warm_seconds,
        max_event_loop_lag_seconds=max(lags, default=0.0),
        error_count=len(errors),
        crossed_resource_count=crossed,
        max_backend_inflight=backend.max_total_inflight,
        max_release_inflight=_FakeSandboxClient.max_close_inflight,
    )


async def _run_fault_wave(aio_mod, wave: int) -> _FaultWaveResult:
    user_id = "fault-capacity-user"
    thread_ids = [f"fault-wave-{wave}-room-{index}" for index in range(8)]
    sandbox_ids = {
        thread_id: aio_mod.AioSandboxProvider._deterministic_sandbox_id(
            thread_id,
            user_id,
        )
        for thread_id in thread_ids
    }
    slow_thread = thread_ids[0]
    slow_sandbox = sandbox_ids[slow_thread]
    cancelled_threads = {thread_ids[2], thread_ids[5]}
    cancelled_sandboxes = {sandbox_ids[thread_id] for thread_id in cancelled_threads}

    backend = _DelayedBackend(aio_mod.SandboxInfo, delay=0.02)
    backend.delay_by_sandbox[slow_sandbox] = 0.12
    backend.unknown_health_sandboxes.add(slow_sandbox)
    backend.destroy_failures_remaining[slow_sandbox] = 1
    provider = _make_provider(aio_mod, backend)
    _FakeSandboxClient.reset_close_metrics(delay=0.02)

    stage_timings: dict[str, dict[str, float]] = {
        "cold": {},
        "release": {},
        "warm": {},
        "destroy": {},
        "recovery": {},
    }
    unexpected_errors = 0
    crossed_resources = 0
    cancellations = 0
    recovered_destroy_failures = 0
    lags: list[float] = []
    stop = asyncio.Event()
    heartbeat = asyncio.create_task(_heartbeat(stop, lags))
    await asyncio.sleep(0)
    wave_started = time.perf_counter()

    try:
        cold_tasks = {
            thread_id: asyncio.create_task(
                _timed_acquire(
                    provider,
                    thread_id,
                    user_id,
                    stage_timings["cold"],
                )
            )
            for thread_id in thread_ids
        }
        await _wait_for_create_started(backend, cancelled_sandboxes)
        for thread_id in cancelled_threads:
            cold_tasks[thread_id].cancel()

        cold_results = await asyncio.wait_for(
            asyncio.gather(*cold_tasks.values(), return_exceptions=True),
            timeout=2,
        )
        cold_successes: dict[str, str] = {}
        for thread_id, result in zip(thread_ids, cold_results, strict=True):
            if thread_id in cancelled_threads:
                if isinstance(result, asyncio.CancelledError):
                    cancellations += 1
                else:
                    unexpected_errors += 1
            elif isinstance(result, str):
                cold_successes[thread_id] = result
            else:
                unexpected_errors += 1

        release_results = await asyncio.wait_for(
            asyncio.gather(
                *(
                    _timed_thread_call(
                        provider.release,
                        sandbox_id,
                        thread_id,
                        stage_timings["release"],
                    )
                    for thread_id, sandbox_id in cold_successes.items()
                ),
                return_exceptions=True,
            ),
            timeout=2,
        )
        unexpected_errors += sum(1 for result in release_results if isinstance(result, BaseException) or result is not None)
        expected_warm = set(cold_successes.values())
        if set(provider._warm_pool) != expected_warm or provider._sandboxes or provider._thread_sandboxes or backend.resource_ids() != expected_warm:
            crossed_resources += 1

        warm_results = await asyncio.wait_for(
            asyncio.gather(
                *(
                    _timed_acquire(
                        provider,
                        thread_id,
                        user_id,
                        stage_timings["warm"],
                    )
                    for thread_id in thread_ids
                ),
                return_exceptions=True,
            ),
            timeout=2,
        )
        warm_ids: dict[str, str] = {}
        for thread_id, result in zip(thread_ids, warm_results, strict=True):
            if isinstance(result, str):
                warm_ids[thread_id] = result
            else:
                unexpected_errors += 1

        if set(warm_ids.values()) != set(sandbox_ids.values()):
            crossed_resources += 1
        for thread_id, expected_id in sandbox_ids.items():
            sandbox = provider.get(expected_id)
            expected_creates = 2 if thread_id in cancelled_threads else 1
            if (
                warm_ids.get(thread_id) != expected_id
                or provider._thread_sandboxes.get((user_id, thread_id)) != expected_id
                or backend.created_by_sandbox.get(expected_id) != thread_id
                or backend.create_calls_by_sandbox.get(expected_id) != expected_creates
                or sandbox is None
                or sandbox.base_url != f"http://fake/{thread_id}/{expected_id}"
            ):
                crossed_resources += 1

        destroy_results = await asyncio.wait_for(
            asyncio.gather(
                *(
                    _timed_thread_call(
                        provider.destroy,
                        sandbox_ids[thread_id],
                        thread_id,
                        stage_timings["destroy"],
                    )
                    for thread_id in thread_ids
                ),
                return_exceptions=True,
            ),
            timeout=2,
        )
        for thread_id, result in zip(thread_ids, destroy_results, strict=True):
            if thread_id == slow_thread:
                if isinstance(result, RuntimeError) and "destroy unknown" in str(result):
                    recovered_destroy_failures += 1
                else:
                    unexpected_errors += 1
            elif isinstance(result, BaseException) or result is not None:
                unexpected_errors += 1

        if set(provider._warm_pool) != {slow_sandbox} or provider._sandboxes or provider._sandbox_infos or provider._thread_sandboxes or provider._last_activity or backend.resource_ids() != {slow_sandbox}:
            crossed_resources += 1

        try:
            await asyncio.wait_for(
                _timed_thread_call(
                    provider.destroy,
                    slow_sandbox,
                    slow_thread,
                    stage_timings["recovery"],
                ),
                timeout=1,
            )
        except BaseException:
            unexpected_errors += 1
    finally:
        stop.set()
        await heartbeat

    del cold_tasks, cold_results, release_results, warm_results, destroy_results
    gc.collect()
    leaked_resources = (
        len(backend.resource_ids())
        + len(provider._sandboxes)
        + len(provider._sandbox_infos)
        + len(provider._thread_sandboxes)
        + len(provider._last_activity)
        + len(provider._warm_pool)
        + len(provider._thread_locks)
        + len(provider._sandbox_locks)
    )
    operation_latencies = tuple(duration for timings in stage_timings.values() for duration in timings.values())
    normal_latencies = tuple(duration for timings in stage_timings.values() for thread_id, duration in timings.items() if thread_id != slow_thread)
    slow_latencies = tuple(duration for timings in stage_timings.values() for thread_id, duration in timings.items() if thread_id == slow_thread)
    slow_backend_latencies = [stage_timings[stage][slow_thread] for stage in ("cold", "warm", "destroy", "recovery")]
    return _FaultWaveResult(
        wave=wave,
        wave_seconds=time.perf_counter() - wave_started,
        operation_latencies=operation_latencies,
        normal_latencies=normal_latencies,
        slow_latencies=slow_latencies,
        loop_lags=tuple(lags),
        unexpected_errors=unexpected_errors,
        crossed_resources=crossed_resources,
        leaked_resources=leaked_resources,
        cancellations=cancellations,
        recovered_destroy_failures=recovered_destroy_failures,
        max_backend_inflight=backend.max_total_inflight,
        max_close_inflight=_FakeSandboxClient.max_close_inflight,
        normal_max_seconds=max(normal_latencies, default=0.0),
        slow_backend_min_seconds=min(slow_backend_latencies),
    )


@pytest.mark.anyio
async def test_independent_command_rooms_scale_with_single_room_delay(
    tmp_path,
    monkeypatch,
):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(aio_mod, "AioSandbox", _FakeSandboxClient)
    monkeypatch.setattr(aio_mod, "wait_for_sandbox_ready", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(aio_mod, "wait_for_sandbox_ready_async", _ready)

    results = {count: await _run_capacity_cycle(aio_mod, count) for count in (1, 3, 5)}
    for result in results.values():
        print(
            "sandbox-capacity",
            f"rooms={result.count}",
            f"cold={result.cold_seconds:.4f}s",
            f"release={result.release_seconds:.4f}s",
            f"warm={result.warm_seconds:.4f}s",
            f"total={result.total_seconds:.4f}s",
            f"max_lag={result.max_event_loop_lag_seconds:.4f}s",
            f"errors={result.error_count}",
            f"crossed={result.crossed_resource_count}",
            f"backend_inflight={result.max_backend_inflight}",
            f"release_inflight={result.max_release_inflight}",
        )
        assert result.error_count == 0
        assert result.crossed_resource_count == 0
        assert result.max_backend_inflight == result.count
        assert result.max_release_inflight == result.count
        assert result.max_event_loop_lag_seconds < 0.1

    single = results[1]
    for count in (3, 5):
        parallel = results[count]
        assert parallel.cold_seconds <= single.cold_seconds + 0.1
        assert parallel.release_seconds <= single.release_seconds + 0.1
        assert parallel.warm_seconds <= single.warm_seconds + 0.1
        assert parallel.total_seconds <= single.total_seconds + 0.3


@pytest.mark.anyio
async def test_eight_command_rooms_survive_five_fault_injected_waves(
    tmp_path,
    monkeypatch,
):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(aio_mod, "AioSandbox", _FakeSandboxClient)
    monkeypatch.setattr(aio_mod, "wait_for_sandbox_ready", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(aio_mod, "wait_for_sandbox_ready_async", _ready)

    waves = [await _run_fault_wave(aio_mod, wave) for wave in range(1, 6)]
    for wave in waves:
        normal_p95_seconds = _percentile(list(wave.normal_latencies), 0.95)
        print(
            "sandbox-fault-wave",
            f"wave={wave.wave}",
            f"elapsed={wave.wave_seconds:.4f}s",
            f"op_p95={_percentile(list(wave.operation_latencies), 0.95):.4f}s",
            f"op_max={max(wave.operation_latencies):.4f}s",
            f"normal_p95={normal_p95_seconds:.4f}s",
            f"normal_max={wave.normal_max_seconds:.4f}s",
            f"slow_min={wave.slow_backend_min_seconds:.4f}s",
            f"loop_max={max(wave.loop_lags, default=0.0):.4f}s",
            f"errors={wave.unexpected_errors}",
            f"crossed={wave.crossed_resources}",
            f"leaked={wave.leaked_resources}",
            f"cancelled={wave.cancellations}",
            f"recovered={wave.recovered_destroy_failures}",
            f"close_inflight={wave.max_close_inflight}",
        )
        assert wave.unexpected_errors == 0
        assert wave.crossed_resources == 0
        assert wave.leaked_resources == 0
        assert wave.cancellations == 2
        assert wave.recovered_destroy_failures == 1
        assert wave.max_backend_inflight == 8
        assert wave.max_close_inflight >= 7
        assert wave.slow_backend_min_seconds >= 0.1
        assert normal_p95_seconds < wave.slow_backend_min_seconds
        assert max(wave.loop_lags, default=0.0) < 0.1

    operation_latencies = [duration for wave in waves for duration in wave.operation_latencies]
    normal_latencies = [duration for wave in waves for duration in wave.normal_latencies]
    slow_latencies = [duration for wave in waves for duration in wave.slow_latencies]
    loop_lags = [lag for wave in waves for lag in wave.loop_lags]
    wave_latencies = [wave.wave_seconds for wave in waves]
    print(
        "sandbox-fault-summary",
        "waves=5 rooms=8",
        f"op_p95={_percentile(operation_latencies, 0.95):.4f}s",
        f"op_max={max(operation_latencies):.4f}s",
        f"normal_p95={_percentile(normal_latencies, 0.95):.4f}s",
        f"normal_max={max(normal_latencies):.4f}s",
        f"slow_p95={_percentile(slow_latencies, 0.95):.4f}s",
        f"slow_max={max(slow_latencies):.4f}s",
        f"wave_p95={_percentile(wave_latencies, 0.95):.4f}s",
        f"wave_max={max(wave_latencies):.4f}s",
        f"loop_p95={_percentile(loop_lags, 0.95):.4f}s",
        f"loop_max={max(loop_lags, default=0.0):.4f}s",
        f"errors={sum(wave.unexpected_errors for wave in waves)}",
        f"crossed={sum(wave.crossed_resources for wave in waves)}",
        f"leaked={sum(wave.leaked_resources for wave in waves)}",
        f"cancelled={sum(wave.cancellations for wave in waves)}",
        f"recovered_destroy_failures={sum(wave.recovered_destroy_failures for wave in waves)}",
    )


@pytest.mark.anyio
async def test_same_command_room_sandbox_operations_remain_serial(
    tmp_path,
    monkeypatch,
):
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(aio_mod, "AioSandbox", _FakeSandboxClient)
    monkeypatch.setattr(aio_mod, "wait_for_sandbox_ready_async", _ready)
    backend = _DelayedBackend(aio_mod.SandboxInfo, delay=0.04)
    provider = _make_provider(aio_mod, backend)
    thread_id = "one-command-room"
    user_id = "capacity-user"
    lags: list[float] = []
    stop = asyncio.Event()
    heartbeat = asyncio.create_task(_heartbeat(stop, lags))
    await asyncio.sleep(0)

    try:
        started = time.perf_counter()
        raw_results = await asyncio.gather(
            *(provider.acquire_async(thread_id, user_id=user_id) for _ in range(8)),
            return_exceptions=True,
        )
        elapsed = time.perf_counter() - started
    finally:
        stop.set()
        await heartbeat

    errors = [result for result in raw_results if isinstance(result, BaseException)]
    sandbox_ids = [result for result in raw_results if isinstance(result, str)]
    sandbox_id = provider._deterministic_sandbox_id(thread_id, user_id)
    print(
        "sandbox-same-room",
        f"callers=8 elapsed={elapsed:.4f}s",
        f"max_lag={max(lags, default=0.0):.4f}s",
        f"errors={len(errors)}",
        f"backend_inflight={backend.max_total_inflight}",
    )

    assert errors == []
    assert sandbox_ids == [sandbox_id] * 8
    assert backend.create_calls == 1
    assert backend.max_total_inflight == 1
    assert backend.max_inflight_by_sandbox[sandbox_id] == 1
    assert elapsed >= 0.32
    assert max(lags, default=0.0) < 0.1
