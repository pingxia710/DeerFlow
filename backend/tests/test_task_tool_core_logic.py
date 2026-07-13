"""Task-tool contract tests for direct Codex CLI delegation."""

import asyncio
import importlib
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

from deerflow.subagents.codex_cli import CodexCliError, CodexCliTaskResult, CodexCliTimeoutError

task_tool_module = importlib.import_module("deerflow.tools.builtins.task_tool")


def _runtime(timeout_seconds: int = 3600):
    return SimpleNamespace(
        state={"thread_data": {"workspace_path": "/tmp/deerflow-thread"}},
        context={
            "thread_id": "thread-1",
            "run_id": "run-1",
            "app_config": SimpleNamespace(subagents=SimpleNamespace(timeout_seconds=timeout_seconds)),
        },
        config={"metadata": {"trace_id": "trace-1"}, "configurable": {}},
    )


def _run_task_tool(**kwargs) -> ToolMessage:
    coroutine = getattr(task_tool_module.task_tool, "coroutine", None)
    assert coroutine is not None
    return asyncio.run(coroutine(**kwargs))


def _patch_audit(monkeypatch, handoffs):
    async def record(**kwargs):
        handoffs.append(kwargs)

    monkeypatch.setattr(task_tool_module, "_record_subagent_handoff_async", record)


def test_task_tool_runs_codex_cli_and_preserves_result(monkeypatch):
    events = []
    handoffs = []
    captured = {}

    async def run(prompt, *, workspace_path, timeout_seconds, model, reasoning_effort):
        captured.update(
            prompt=prompt,
            workspace_path=workspace_path,
            timeout_seconds=timeout_seconds,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        return CodexCliTaskResult(result="completed work")

    _patch_audit(monkeypatch, handoffs)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module, "run_codex_cli_task", run)

    result = _run_task_tool(
        runtime=_runtime(timeout_seconds=3600),
        description="Audit backend",
        prompt="Inspect the backend and return findings.",
        subagent_type="any-advisory-label",
        tool_call_id="call-1",
    )

    assert result.content == "Task Succeeded. Result: completed work"
    assert captured == {
        "prompt": "Inspect the backend and return findings.",
        "workspace_path": "/tmp/deerflow-thread",
        "timeout_seconds": 3600,
        "model": None,
        "reasoning_effort": None,
    }
    assert [event["type"] for event in events] == ["task_started", "task_completed"]
    assert [handoff["status"] for handoff in handoffs] == ["started", "completed"]
    assert handoffs[-1]["subagent_type"] == "any-advisory-label"


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_event"),
    [
        (CodexCliError("CLI failed"), "failed", "task_failed"),
        (CodexCliTimeoutError("CLI timed out"), "timed_out", "task_timed_out"),
    ],
)
def test_task_tool_records_cli_failure(monkeypatch, error, expected_status, expected_event):
    events = []
    handoffs = []

    async def run(*_args, **_kwargs):
        raise error

    _patch_audit(monkeypatch, handoffs)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module, "run_codex_cli_task", run)

    result = _run_task_tool(
        runtime=_runtime(),
        description="Audit backend",
        prompt="Inspect the backend.",
        subagent_type="general-purpose",
        tool_call_id="call-2",
    )

    assert result.content == f"Task {'timed out' if expected_status == 'timed_out' else 'failed'}. Error: {error}"
    assert result.additional_kwargs["subagent_status"] == expected_status
    assert events[-1]["type"] == expected_event
    assert handoffs[-1]["status"] == expected_status


def test_task_tool_records_parent_cancellation(monkeypatch):
    events = []
    handoffs = []

    async def run(*_args, **_kwargs):
        raise asyncio.CancelledError

    _patch_audit(monkeypatch, handoffs)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module, "run_codex_cli_task", run)

    with pytest.raises(asyncio.CancelledError):
        _run_task_tool(
            runtime=_runtime(),
            description="Audit backend",
            prompt="Inspect the backend.",
            subagent_type="general-purpose",
            tool_call_id="call-3",
        )

    assert events[-1]["type"] == "task_cancelled"
    assert handoffs[-1]["status"] == "cancelled"
