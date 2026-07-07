"""Tests for process-wide subagent limiter/queue."""

import importlib
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

_MOCKED_MODULE_NAMES = [
    "deerflow.agents",
    "deerflow.agents.thread_state",
    "deerflow.agents.middlewares",
    "deerflow.agents.middlewares.thread_data_middleware",
    "deerflow.sandbox",
    "deerflow.sandbox.middleware",
    "deerflow.sandbox.security",
    "deerflow.models",
    "deerflow.skills.storage",
]


def _clear_stale_executor_package_attr() -> None:
    subagents_pkg = sys.modules.get("deerflow.subagents")
    if subagents_pkg is not None and hasattr(subagents_pkg, "executor"):
        delattr(subagents_pkg, "executor")


class FakeFuture:
    def __init__(self):
        self.callbacks = []
        self.cancelled = False
        self.exc = None
        self.value = None

    def add_done_callback(self, callback):
        self.callbacks.append(callback)

    def result(self):
        if self.exc is not None:
            raise self.exc
        return self.value

    def cancel(self):
        self.cancelled = True

    def complete(self):
        for callback in list(self.callbacks):
            callback(self)


class FakeTimer:
    created = []

    def __init__(self, interval, function):
        self.interval = interval
        self.function = function
        self.daemon = False
        self.started = False
        self.cancelled = False
        type(self).created.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


@pytest.fixture
def executor_module(monkeypatch):
    original_modules = {name: sys.modules.get(name) for name in _MOCKED_MODULE_NAMES}
    original_executor = sys.modules.get("deerflow.subagents.executor")
    sys.modules.pop("deerflow.subagents.executor", None)
    _clear_stale_executor_package_attr()
    for name in _MOCKED_MODULE_NAMES:
        sys.modules[name] = MagicMock()
    storage_module = ModuleType("deerflow.skills.storage")
    storage_module.get_or_new_skill_storage = lambda **kwargs: SimpleNamespace(load_skills=lambda *, enabled_only: [])
    sys.modules["deerflow.skills.storage"] = storage_module

    mod = importlib.import_module("deerflow.subagents.executor")
    mod.get_app_config = lambda: SimpleNamespace(subagents=SimpleNamespace(process_wide_max_concurrent=2, process_wide_queue_size=1))
    FakeTimer.created = []
    monkeypatch.setattr(mod.threading, "Timer", FakeTimer)
    futures = []

    def fake_submit(context, factory):
        coro = factory()
        try:
            coro.close()
        except AttributeError:
            pass
        future = FakeFuture()
        future.value = coro.cr_frame.f_locals.get("result") if getattr(coro, "cr_frame", None) is not None else None
        futures.append(future)
        return future

    monkeypatch.setattr(mod, "_submit_to_isolated_loop_in_context", fake_submit)
    mod._background_tasks.clear()
    mod._subagent_pending_queue.clear()
    mod._subagent_running_count = 0
    mod._test_futures = futures

    yield mod

    mod._background_tasks.clear()
    mod._subagent_pending_queue.clear()
    mod._subagent_running_count = 0
    for name in _MOCKED_MODULE_NAMES:
        if original_modules[name] is not None:
            sys.modules[name] = original_modules[name]
        else:
            sys.modules.pop(name, None)
    if original_executor is not None:
        sys.modules["deerflow.subagents.executor"] = original_executor
    else:
        sys.modules.pop("deerflow.subagents.executor", None)
    _clear_stale_executor_package_attr()


@pytest.fixture
def make_executor(executor_module):
    from deerflow.subagents.config import SubagentConfig

    def _make(name="researcher", *, thread_id=None, run_id=None, trace_id="trace"):
        return executor_module.SubagentExecutor(
            SubagentConfig(name=name, description="d", system_prompt="p", tools=[], timeout_seconds=30),
            tools=[],
            trace_id=trace_id,
            thread_id=thread_id,
            run_id=run_id,
        )

    return _make


def test_limit_reached_queues_task_pending(executor_module, make_executor):
    ex = make_executor()
    ids = [ex.execute_async(f"task {i}") for i in range(3)]
    assert len(executor_module._test_futures) == 2
    assert executor_module.get_background_task_result(ids[2]).status == executor_module.SubagentStatus.PENDING
    assert len(FakeTimer.created) == 2


def test_process_wide_admitted_cap_is_shared_across_executors(executor_module, make_executor):
    executor_module.get_app_config = lambda: SimpleNamespace(subagents=SimpleNamespace(process_wide_max_concurrent=2, process_wide_queue_size=2))
    ex_a = make_executor("researcher-a")
    ex_b = make_executor("researcher-b")

    first = ex_a.execute_async("task a1")
    second = ex_b.execute_async("task b1")
    third = ex_b.execute_async("task b2")

    assert len(executor_module._test_futures) == 2
    assert executor_module.get_background_task_result(first).status == executor_module.SubagentStatus.RUNNING
    assert executor_module.get_background_task_result(second).status == executor_module.SubagentStatus.RUNNING
    assert executor_module.get_background_task_result(third).status == executor_module.SubagentStatus.PENDING
    assert executor_module._subagent_running_count == 2


def test_completion_releases_slot_and_drains_next(executor_module, make_executor):
    ex = make_executor()
    ids = [ex.execute_async(f"task {i}") for i in range(3)]
    executor_module._test_futures[0].complete()
    assert len(executor_module._test_futures) == 3
    assert executor_module.get_background_task_result(ids[2]).status == executor_module.SubagentStatus.RUNNING
    assert executor_module._subagent_running_count == 2


def test_completed_fake_future_payload_is_terminal_and_drains_queue(executor_module, make_executor):
    executor_module.get_app_config = lambda: SimpleNamespace(subagents=SimpleNamespace(process_wide_max_concurrent=1, process_wide_queue_size=2))
    ex = make_executor()
    ids = [ex.execute_async(f"task {i}") for i in range(2)]

    first_result = executor_module.get_background_task_result(ids[0])
    assert first_result.status == executor_module.SubagentStatus.RUNNING
    assert executor_module.get_background_task_result(ids[1]).status == executor_module.SubagentStatus.PENDING

    executor_module._test_futures[0].value = first_result
    first_result.try_set_terminal(
        executor_module.SubagentStatus.COMPLETED,
        result="fake terminal payload",
        ai_messages=[{"type": "ai", "content": "done"}],
    )
    executor_module._test_futures[0].complete()

    completed = executor_module.get_background_task_result(ids[0])
    assert completed.status == executor_module.SubagentStatus.COMPLETED
    assert completed.result == "fake terminal payload"
    assert completed.ai_messages == [{"type": "ai", "content": "done"}]
    assert len(executor_module._test_futures) == 2
    assert executor_module.get_background_task_result(ids[1]).status == executor_module.SubagentStatus.RUNNING
    assert executor_module._subagent_running_count == 1

    second_result = executor_module.get_background_task_result(ids[1])
    executor_module._test_futures[1].value = second_result
    second_result.try_set_terminal(executor_module.SubagentStatus.COMPLETED, result="second payload")
    executor_module._test_futures[1].complete()

    assert executor_module.get_background_task_result(ids[1]).status == executor_module.SubagentStatus.COMPLETED
    assert executor_module.get_background_task_result(ids[1]).result == "second payload"
    assert sum(executor_module.get_background_task_result(tid).status == executor_module.SubagentStatus.COMPLETED for tid in ids) == 2
    assert executor_module._subagent_running_count == 0


def test_pending_cancellation_never_submits(executor_module, make_executor):
    ex = make_executor()
    ids = [ex.execute_async(f"task {i}") for i in range(3)]
    executor_module.request_cancel_background_task(ids[2])
    result = executor_module.get_background_task_result(ids[2])
    assert result.status == executor_module.SubagentStatus.CANCELLED
    assert result.error == "Cancelled by user"
    executor_module._test_futures[0].complete()
    assert len(executor_module._test_futures) == 2


def test_queue_overflow_returns_failed_task_id(executor_module, make_executor):
    ex = make_executor()
    ids = [ex.execute_async(f"task {i}") for i in range(4)]
    result = executor_module.get_background_task_result(ids[3])
    assert result is not None
    assert result.status == executor_module.SubagentStatus.FAILED
    assert result.error == "Subagent queue is full; try again later"
    assert len(executor_module._test_futures) == 2


def test_timeout_timer_only_created_after_admission(executor_module, make_executor):
    ex = make_executor()
    ids = [ex.execute_async(f"task {i}") for i in range(3)]
    assert len(FakeTimer.created) == 2
    assert executor_module.get_background_task_result(ids[2]).started_at is None
    executor_module._test_futures[0].complete()
    assert len(FakeTimer.created) == 3
    assert FakeTimer.created[-1].started is True


def test_timeout_without_done_callback_releases_slot_and_drains_queue(executor_module, make_executor):
    executor_module.get_app_config = lambda: SimpleNamespace(subagents=SimpleNamespace(process_wide_max_concurrent=1, process_wide_queue_size=2))
    ex = make_executor()
    ids = [ex.execute_async(f"task {i}") for i in range(2)]

    assert len(executor_module._test_futures) == 1
    assert executor_module.get_background_task_result(ids[0]).status == executor_module.SubagentStatus.RUNNING
    assert executor_module.get_background_task_result(ids[1]).status == executor_module.SubagentStatus.PENDING

    FakeTimer.created[0].function()

    assert executor_module.get_background_task_result(ids[0]).status == executor_module.SubagentStatus.TIMED_OUT
    assert executor_module._test_futures[0].cancelled is True
    assert len(executor_module._test_futures) == 2
    assert executor_module.get_background_task_result(ids[1]).status == executor_module.SubagentStatus.RUNNING
    assert executor_module._subagent_running_count == 1


def test_timeout_then_late_future_completion_does_not_double_release(executor_module, make_executor):
    executor_module.get_app_config = lambda: SimpleNamespace(subagents=SimpleNamespace(process_wide_max_concurrent=1, process_wide_queue_size=3))
    ex = make_executor()
    ids = [ex.execute_async(f"task {i}") for i in range(3)]

    assert len(executor_module._test_futures) == 1
    FakeTimer.created[0].function()

    assert len(executor_module._test_futures) == 2
    assert executor_module.get_background_task_result(ids[0]).status == executor_module.SubagentStatus.TIMED_OUT
    assert executor_module.get_background_task_result(ids[1]).status == executor_module.SubagentStatus.RUNNING
    assert executor_module.get_background_task_result(ids[2]).status == executor_module.SubagentStatus.PENDING
    assert executor_module._subagent_running_count == 1

    executor_module._test_futures[0].complete()

    assert len(executor_module._test_futures) == 2
    assert executor_module.get_background_task_result(ids[1]).status == executor_module.SubagentStatus.RUNNING
    assert executor_module.get_background_task_result(ids[2]).status == executor_module.SubagentStatus.PENDING
    assert executor_module._subagent_running_count == 1


def test_concurrent_timeout_and_late_completion_release_once(executor_module, make_executor, monkeypatch):
    import threading

    executor_module.get_app_config = lambda: SimpleNamespace(subagents=SimpleNamespace(process_wide_max_concurrent=1, process_wide_queue_size=3))
    release_count = 0
    original_release = executor_module._release_subagent_slot_and_drain

    def counted_release():
        nonlocal release_count
        release_count += 1
        original_release()

    monkeypatch.setattr(executor_module, "_release_subagent_slot_and_drain", counted_release)

    ex = make_executor()
    ids = [ex.execute_async(f"task {i}") for i in range(3)]

    timeout_thread = threading.Thread(target=FakeTimer.created[0].function)
    complete_thread = threading.Thread(target=executor_module._test_futures[0].complete)
    timeout_thread.start()
    complete_thread.start()
    timeout_thread.join()
    complete_thread.join()

    assert release_count == 1
    assert len(executor_module._test_futures) == 2
    assert executor_module.get_background_task_result(ids[1]).status == executor_module.SubagentStatus.RUNNING
    assert executor_module.get_background_task_result(ids[2]).status == executor_module.SubagentStatus.PENDING
    assert executor_module._subagent_running_count == 1


def test_deterministic_two_session_mock_pressure_drains_to_zero(executor_module, make_executor):
    """Different executor instances simulate distinct thread/run sessions; no production API change."""
    executor_module.get_app_config = lambda: SimpleNamespace(subagents=SimpleNamespace(process_wide_max_concurrent=12, process_wide_queue_size=12))
    groups = [[make_executor(f"session-{g}-worker-{i}") for i in range(6)] for g in range(2)]

    task_ids = [ex.execute_async(f"session {g} task {i}") for g, group in enumerate(groups) for i, ex in enumerate(group)]

    assert len(executor_module._test_futures) == 12
    assert sum(executor_module.get_background_task_result(tid).status == executor_module.SubagentStatus.RUNNING for tid in task_ids) == 12
    assert sum(executor_module.get_background_task_result(tid).status == executor_module.SubagentStatus.PENDING for tid in task_ids) == 0
    assert executor_module._subagent_running_count == 12

    for future in list(executor_module._test_futures):
        future.complete()

    assert len(executor_module._test_futures) == 12
    assert executor_module._subagent_running_count == 0
    assert sum(executor_module.get_background_task_result(tid).status == executor_module.SubagentStatus.PENDING for tid in task_ids) == 0

    for future in list(executor_module._test_futures[4:]):
        future.complete()

    assert all(executor_module.get_background_task_result(tid).status.is_terminal is False for tid in task_ids)
    assert executor_module._subagent_running_count == 0


def test_deterministic_command_room_like_mock_pressure_counts(executor_module, make_executor):
    """Five executor groups approximate command-room sessions with deterministic fake futures only."""
    executor_module.get_app_config = lambda: SimpleNamespace(subagents=SimpleNamespace(process_wide_max_concurrent=12, process_wide_queue_size=30))
    groups = [[make_executor(f"command-room-{g}-worker-{i}") for i in range(6)] for g in range(5)]

    task_ids = [ex.execute_async(f"command-room {g} task {i}") for g, group in enumerate(groups) for i, ex in enumerate(group)]

    assert len(task_ids) == 30
    assert len(executor_module._test_futures) == 12
    assert sum(executor_module.get_background_task_result(tid).status == executor_module.SubagentStatus.RUNNING for tid in task_ids) == 12
    assert sum(executor_module.get_background_task_result(tid).status == executor_module.SubagentStatus.PENDING for tid in task_ids) == 18
    assert executor_module._subagent_running_count == 12

    for future in list(executor_module._test_futures):
        future.complete()

    assert len(executor_module._test_futures) == 24
    assert executor_module._subagent_running_count == 12
    assert sum(executor_module.get_background_task_result(tid).status == executor_module.SubagentStatus.PENDING for tid in task_ids) == 6

    for future in list(executor_module._test_futures[12:]):
        future.complete()
    assert len(executor_module._test_futures) == 30
    for future in list(executor_module._test_futures[24:]):
        future.complete()

    assert all(executor_module.get_background_task_result(tid).status.is_terminal is False for tid in task_ids)
    assert executor_module._subagent_running_count == 0


def test_pending_polling_timeout_cancels_without_admission_leak(executor_module, make_executor):
    """A parent-side polling timeout/cancel of a queued task must not later ghost-start or leak a slot."""
    executor_module.get_app_config = lambda: SimpleNamespace(subagents=SimpleNamespace(process_wide_max_concurrent=1, process_wide_queue_size=2))
    ex = make_executor()
    first = ex.execute_async("running")
    pending = ex.execute_async("pending")

    assert executor_module.get_background_task_result(first).status == executor_module.SubagentStatus.RUNNING
    assert executor_module.get_background_task_result(pending).status == executor_module.SubagentStatus.PENDING
    assert executor_module._subagent_running_count == 1
    assert len(executor_module._test_futures) == 1

    executor_module.request_cancel_background_task(pending)
    executor_module.cleanup_background_task(pending)
    assert executor_module.get_background_task_result(pending) is None

    executor_module._test_futures[0].complete()
    assert len(executor_module._test_futures) == 1
    assert executor_module._subagent_running_count == 0


def test_p2_local_five_session_rehearsal_cap_round_robin_and_run_scoping(executor_module, make_executor):
    """Deterministic fake 5 sessions x 6 tasks rehearsal; no real provider/executor is invoked."""
    executor_module.get_app_config = lambda: SimpleNamespace(subagents=SimpleNamespace(process_wide_max_concurrent=12, process_wide_queue_size=30))
    sessions = [
        {
            "thread_id": f"thread-{session_index}",
            "run_id": f"run-{session_index}",
            "executors": [
                make_executor(
                    f"command-room-{session_index}-worker-{task_index}",
                    thread_id=f"thread-{session_index}",
                    run_id=f"run-{session_index}",
                    trace_id=f"trace-{session_index}-{task_index}",
                )
                for task_index in range(6)
            ],
        }
        for session_index in range(5)
    ]

    task_rows = []
    for session_index, session in enumerate(sessions):
        for task_index, ex in enumerate(session["executors"]):
            reused_task_id = f"task-{task_index}"
            returned_task_id = ex.execute_async(f"session {session_index} task {task_index}", task_id=reused_task_id)
            task_rows.append((session_index, task_index, session["thread_id"], session["run_id"], returned_task_id))

    assert len(task_rows) == 30
    assert len(executor_module._test_futures) == 12
    assert executor_module._subagent_running_count == 12
    assert sum(executor_module.get_background_task_result(task_id, run_id).status == executor_module.SubagentStatus.RUNNING for _, _, _, run_id, task_id in task_rows) == 12
    assert sum(executor_module.get_background_task_result(task_id, run_id).status == executor_module.SubagentStatus.PENDING for _, _, _, run_id, task_id in task_rows) == 18

    # Reused task ids are isolated by run_id; unscoped or wrong-run lookups must not replay another session's result.
    assert executor_module.get_background_task_result("task-0") is None
    assert executor_module.get_background_task_result("task-0", "run-missing") is None
    for session_index, _, _, run_id, task_id in task_rows[::6]:
        result = executor_module.get_background_task_result(task_id, run_id)
        assert result is not None
        assert result.task_id == "task-0"
        assert result.trace_id.startswith(f"trace-{session_index}-")

    first_wave_by_session = [row[0] for row in task_rows if executor_module.get_background_task_result(row[4], row[3]).status == executor_module.SubagentStatus.RUNNING]
    assert first_wave_by_session == [0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1]

    # Completing the first 12 drains one pending item per session in round-robin order, then continues from the oldest buckets.
    for future, row in zip(list(executor_module._test_futures), task_rows[:12], strict=True):
        _, _, _, run_id, task_id = row
        result = executor_module.get_background_task_result(task_id, run_id)
        future.value = result
        result.try_set_terminal(executor_module.SubagentStatus.COMPLETED, result=f"terminal:{run_id}:{task_id}")
        future.complete()

    assert len(executor_module._test_futures) == 24
    second_wave_rows = [row for row in task_rows if executor_module.get_background_task_result(row[4], row[3]).status == executor_module.SubagentStatus.RUNNING]
    assert [row[0] for row in second_wave_rows] == [2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3]
    assert sum(executor_module.get_background_task_result(task_id, run_id).status == executor_module.SubagentStatus.PENDING for _, _, _, run_id, task_id in task_rows) == 6

    # Mark all admitted tasks terminal before completing their fake futures; callbacks must not double-release or duplicate replay state.
    for future, row in zip(executor_module._test_futures[12:], second_wave_rows, strict=True):
        _, _, _, run_id, task_id = row
        result = executor_module.get_background_task_result(task_id, run_id)
        future.value = result
        result.try_set_terminal(executor_module.SubagentStatus.COMPLETED, result=f"terminal:{run_id}:{task_id}")
        result.try_set_terminal(executor_module.SubagentStatus.FAILED, error="duplicate terminal must be ignored")
        future.complete()

    assert len(executor_module._test_futures) == 30
    third_wave_rows = [row for row in task_rows if executor_module.get_background_task_result(row[4], row[3]).status == executor_module.SubagentStatus.RUNNING]
    assert [row[0] for row in third_wave_rows] == [4, 4, 4, 4, 4, 4]
    assert executor_module._subagent_running_count == 6

    for future, row in zip(executor_module._test_futures[24:], third_wave_rows, strict=True):
        _, _, _, run_id, task_id = row
        result = executor_module.get_background_task_result(task_id, run_id)
        future.value = result
        result.try_set_terminal(executor_module.SubagentStatus.COMPLETED, result=f"terminal:{run_id}:{task_id}")
        future.complete()

    assert executor_module._subagent_running_count == 0
    assert all(executor_module.get_background_task_result(task_id, run_id).status == executor_module.SubagentStatus.COMPLETED for _, _, _, run_id, task_id in task_rows[12:])
    assert all(executor_module.get_background_task_result(task_id, run_id).result == f"terminal:{run_id}:{task_id}" for _, _, _, run_id, task_id in task_rows[12:])
