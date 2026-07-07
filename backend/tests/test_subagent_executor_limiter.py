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

    def add_done_callback(self, callback):
        self.callbacks.append(callback)

    def result(self):
        if self.exc is not None:
            raise self.exc
        return None

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
        future = FakeFuture()
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

    def _make(name="researcher"):
        return executor_module.SubagentExecutor(SubagentConfig(name=name, description="d", system_prompt="p", tools=[], timeout_seconds=30), tools=[], trace_id="trace")

    return _make


def test_limit_reached_queues_task_pending(executor_module, make_executor):
    ex = make_executor()
    ids = [ex.execute_async(f"task {i}") for i in range(3)]
    assert len(executor_module._test_futures) == 2
    assert executor_module.get_background_task_result(ids[2]).status == executor_module.SubagentStatus.PENDING
    assert len(FakeTimer.created) == 2


def test_completion_releases_slot_and_drains_next(executor_module, make_executor):
    ex = make_executor()
    ids = [ex.execute_async(f"task {i}") for i in range(3)]
    executor_module._test_futures[0].complete()
    assert len(executor_module._test_futures) == 3
    assert executor_module.get_background_task_result(ids[2]).status == executor_module.SubagentStatus.RUNNING
    assert executor_module._subagent_running_count == 2


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
