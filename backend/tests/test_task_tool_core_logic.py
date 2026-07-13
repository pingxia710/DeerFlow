"""Task-tool contract tests for direct Codex CLI delegation."""

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

task_tool_module = importlib.import_module("deerflow.tools.builtins.task_tool")


def _runtime(
    timeout_seconds: int = 3600,
    *,
    workspace_path: str = "/tmp/deerflow-thread",
    uploads_path: str | None = None,
    outputs_path: str | None = None,
    app_config=None,
    round_id: str | None = None,
):
    app_config = app_config or SimpleNamespace(subagents=SimpleNamespace(timeout_seconds=timeout_seconds))
    thread_data = {"workspace_path": workspace_path}
    if uploads_path is not None:
        thread_data["uploads_path"] = uploads_path
    if outputs_path is not None:
        thread_data["outputs_path"] = outputs_path
    context = {
        "thread_id": "thread-1",
        "run_id": "run-1",
        "app_config": app_config,
    }
    if round_id is not None:
        context["round_id"] = round_id
    return SimpleNamespace(
        state={"thread_data": thread_data},
        context=context,
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

    async def run(
        prompt,
        *,
        workspace_path,
        timeout_seconds,
        model,
        reasoning_effort,
        sandbox_mode,
        additional_writable_paths,
    ):
        captured.update(
            prompt=prompt,
            workspace_path=workspace_path,
            timeout_seconds=timeout_seconds,
            model=model,
            reasoning_effort=reasoning_effort,
            sandbox_mode=sandbox_mode,
            additional_writable_paths=additional_writable_paths,
        )
        return "completed work"

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

    assert result.content == "completed work"
    assert captured == {
        "prompt": (
            "# Professional role: any-advisory-label\n\n"
            'Work as the professional role "any-advisory-label" selected by the lead AI.\n\n'
            "# Command Room task\n\nInspect the backend and return findings.\n\n"
            "# Applicable project instructions\n\n"
            "Before working on any target path, locate and read the complete AGENTS.md instruction chain "
            "that applies to that path, including ancestor, project, and nearer subdirectory files. Follow "
            "the nearest applicable rules when instructions conflict. Do not edit an AGENTS.md file unless "
            "this task explicitly authorizes that edit.\n\n"
            "# DeerFlow task paths\n\n"
            f"- Workspace: {Path('/tmp/deerflow-thread').resolve()}\n"
            "- Any /mnt/user-data paths in the handoff refer to the matching host paths above."
        ),
        "workspace_path": str(Path("/tmp/deerflow-thread").resolve()),
        "timeout_seconds": 3600,
        "model": None,
        "reasoning_effort": None,
        "sandbox_mode": "workspace-write",
        "additional_writable_paths": [],
    }
    assert [event["type"] for event in events] == ["task_started", "task_completed"]
    assert [handoff["status"] for handoff in handoffs] == ["started", "completed"]
    assert handoffs[-1]["subagent_type"] == "any-advisory-label"


def test_task_tool_creates_fresh_lazy_workspace_before_codex(monkeypatch, tmp_path):
    events = []
    handoffs = []
    workspace = tmp_path / "fresh-thread" / "user-data" / "workspace"

    async def run(_prompt, *, workspace_path, **_kwargs):
        assert workspace_path == str(workspace)
        assert Path(workspace_path).is_dir()
        return "done"

    _patch_audit(monkeypatch, handoffs)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module, "run_codex_cli_task", run)

    result = _run_task_tool(
        runtime=_runtime(workspace_path=str(workspace)),
        description="Fresh task",
        prompt="Run immediately on a fresh thread.",
        subagent_type="general-purpose",
        tool_call_id="call-fresh",
    )

    assert result.content == "done"
    assert workspace.is_dir()


def test_task_tool_records_workspace_preparation_failure(monkeypatch):
    events = []
    handoffs = []

    def fail_workspace(*_args, **_kwargs):
        raise PermissionError("workspace denied")

    _patch_audit(monkeypatch, handoffs)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module, "ensure_directory_no_symlinks", fail_workspace)

    result = _run_task_tool(
        runtime=_runtime(workspace_path="/tmp/unavailable-workspace"),
        description="Fresh task",
        prompt="Run immediately.",
        subagent_type="general-purpose",
        tool_call_id="call-workspace-failed",
    )

    assert result.additional_kwargs["subagent_status"] == "failed"
    assert [event["type"] for event in events] == ["task_failed"]
    assert [handoff["status"] for handoff in handoffs] == ["failed"]


def test_task_tool_combines_selected_professional_role_prompt(monkeypatch, tmp_path):
    events = []
    handoffs = []
    captured = {}
    subagents = SimpleNamespace(
        timeout_seconds=3600,
        reasoning_effort="xhigh",
        custom_agents={
            "contract-reviewer": SimpleNamespace(
                description="Reviews contracts.",
                system_prompt="Act as an independent contract reviewer.",
                model="gpt-5.6-terra",
            )
        },
        agents={},
        get_model_for=lambda _name: "gpt-5.6-terra",
    )
    app_config = SimpleNamespace(
        subagents=subagents,
        get_model_config=lambda name: SimpleNamespace(model="gpt-5.6-terra") if name == "gpt-5.6-terra" else None,
    )

    async def run(prompt, **kwargs):
        captured.update(prompt=prompt, **kwargs)
        return "review complete"

    _patch_audit(monkeypatch, handoffs)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module, "run_codex_cli_task", run)

    workspace = tmp_path / "workspace"
    uploads = tmp_path / "uploads"
    outputs = tmp_path / "outputs"
    result = _run_task_tool(
        runtime=_runtime(
            workspace_path=str(workspace),
            uploads_path=str(uploads),
            outputs_path=str(outputs),
            app_config=app_config,
        ),
        description="Review contract",
        prompt="Check the API response shape.",
        subagent_type="contract-reviewer",
        tool_call_id="call-role",
    )

    assert result.content == "review complete"
    assert captured["prompt"] == (
        "# Professional role: contract-reviewer\n\n"
        "Act as an independent contract reviewer.\n\n"
        "# Command Room task\n\n"
        "Check the API response shape.\n\n"
        "# Applicable project instructions\n\n"
        "Before working on any target path, locate and read the complete AGENTS.md instruction chain "
        "that applies to that path, including ancestor, project, and nearer subdirectory files. Follow "
        "the nearest applicable rules when instructions conflict. Do not edit an AGENTS.md file unless "
        "this task explicitly authorizes that edit.\n\n"
        "# DeerFlow task paths\n\n"
        f"- Workspace: {workspace}\n"
        f"- Uploaded files (read): {uploads}\n"
        f"- Output artifacts (write): {outputs}\n"
        "- Any /mnt/user-data paths in the handoff refer to the matching host paths above."
    )
    assert captured["model"] == "gpt-5.6-terra"
    assert captured["reasoning_effort"] == "xhigh"
    assert captured["additional_writable_paths"] == [str(outputs)]
    assert workspace.is_dir()
    assert uploads.is_dir()
    assert outputs.is_dir()
    assert handoffs[0]["prompt"] == captured["prompt"]


def test_task_worker_prompt_falls_back_to_role_description_and_keeps_unknown_label():
    general_prompt = task_tool_module._task_worker_prompt(
        None,
        "general-purpose",
        "Implement the change.",
        {"workspace_path": "/tmp/workspace"},
    )
    unknown_prompt = task_tool_module._task_worker_prompt(
        None,
        "specialist-not-yet-registered",
        "Inspect the target.",
        {"workspace_path": "/tmp/workspace"},
    )

    assert "# Professional role: general-purpose" in general_prompt
    assert "A one-shot general execution AI" in general_prompt
    assert "# Professional role: specialist-not-yet-registered" in unknown_prompt
    assert 'Work as the professional role "specialist-not-yet-registered"' in unknown_prompt
    assert "read the complete AGENTS.md instruction chain" in unknown_prompt


def test_task_tool_stamps_round_identity_on_terminal_message(monkeypatch, tmp_path):
    events = []
    handoffs = []

    async def run(*_args, **_kwargs):
        return "round result"

    _patch_audit(monkeypatch, handoffs)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module, "run_codex_cli_task", run)

    result = _run_task_tool(
        runtime=_runtime(workspace_path=str(tmp_path / "workspace"), round_id="round-1"),
        description="Round task",
        prompt="Return the result.",
        subagent_type="general-purpose",
        tool_call_id="call-round",
    )

    assert result.additional_kwargs == {
        "subagent_status": "completed",
        "round_id": "round-1",
    }
    assert [event["round_id"] for event in events] == ["round-1", "round-1"]


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_event"),
    [
        (RuntimeError("CLI failed"), "failed", "task_failed"),
        (TimeoutError("CLI timed out"), "timed_out", "task_timed_out"),
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
