"""Task-tool contract tests for direct Codex CLI delegation."""

import asyncio
import importlib
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal

task_tool_module = importlib.import_module("deerflow.tools.builtins.task_tool")


def _runtime(
    timeout_seconds: int = 3600,
    *,
    workspace_path: str = "/tmp/deerflow-thread",
    uploads_path: str | None = None,
    outputs_path: str | None = None,
    app_config=None,
    round_id: str | None = None,
    agent_name: str | None = None,
    background_dispatcher=None,
    run_id: str = "run-1",
):
    app_config = app_config or SimpleNamespace(subagents=SimpleNamespace(timeout_seconds=timeout_seconds))
    thread_data = {"workspace_path": workspace_path}
    if uploads_path is not None:
        thread_data["uploads_path"] = uploads_path
    if outputs_path is not None:
        thread_data["outputs_path"] = outputs_path
    context = {
        "thread_id": "thread-1",
        "run_id": run_id,
        "app_config": app_config,
    }
    if round_id is not None:
        context["round_id"] = round_id
    if agent_name is not None:
        context["agent_name"] = agent_name
    if background_dispatcher is not None:
        context["__command_room_background_dispatcher"] = background_dispatcher
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


def test_command_room_task_schema_exposes_optional_work_package_id():
    field = task_tool_module.task_tool.args["work_package_id"]

    assert field["title"] == "Work Package Id"
    assert "Optional namespace label" in field["description"]


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


def test_command_room_task_prompt_includes_shared_ai_workspace(monkeypatch, tmp_path):
    events = []
    handoffs = []
    captured = {}
    workspace = tmp_path / "thread" / "user-data" / "workspace"

    async def run(prompt, **kwargs):
        captured.update(prompt=prompt, **kwargs)
        match = re.search(r"Write your complete natural-language handoff to: (.+)", prompt)
        assert match is not None
        Path(match.group(1)).write_text("# AI-authored planning angle\n", encoding="utf-8")
        return "done"

    _patch_audit(monkeypatch, handoffs)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module, "run_codex_cli_task", run)

    result = _run_task_tool(
        runtime=_runtime(workspace_path=str(workspace), agent_name="command-room"),
        description="Plan with evidence",
        prompt="Work with the shared AI-AI files.",
        subagent_type="planner",
        tool_call_id="call-ai-workspace",
        container="planning",
        container_artifact="planning-forward",
    )

    ai_workspace = workspace / "command-room-loop" / "thread-1"
    assert result.content == "done"
    assert (ai_workspace / "01-planning" / "spec.md").is_file()
    assert (ai_workspace / "02-technical-design" / "technical-plan.md").is_file()
    assert (ai_workspace / "03-delivery" / "README.md").is_file()
    assert "# AI-AI workspace" in captured["prompt"]
    assert str(ai_workspace / "01-planning" / "spec.md") in captured["prompt"]
    assert str(ai_workspace / "02-technical-design" / "technical-plan.md") in captured["prompt"]
    assert "# Required Command Room handoff" in captured["prompt"]
    assert "Factual label: Optional Planning" in captured["prompt"]
    assert "Artifact: planning-forward" in captured["prompt"]
    assert result.additional_kwargs["command_room_container"] == "planning"
    assert result.additional_kwargs["container_artifact_written"] is True
    assert events[-1]["command_room_container"] == "planning"
    assert events[-1]["container_artifact_written"] is True


def test_command_room_task_dispatches_in_background_when_runtime_service_is_available(monkeypatch, tmp_path):
    events = []
    handoffs = []
    run_calls = []

    class FakeDispatcher:
        def __init__(self):
            self.jobs = []

        async def dispatch(self, job):
            self.jobs.append(job)

    dispatcher = FakeDispatcher()

    async def run(prompt, **_kwargs):
        run_calls.append(prompt)
        match = re.search(r"Write your complete natural-language handoff to: (.+)", prompt)
        assert match is not None
        Path(match.group(1)).write_text("# Executed\n", encoding="utf-8")
        return "background work completed"

    _patch_audit(monkeypatch, handoffs)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module, "run_codex_cli_task", run)

    result = _run_task_tool(
        runtime=_runtime(
            workspace_path=str(tmp_path / "workspace"),
            agent_name="command-room",
            background_dispatcher=dispatcher,
        ),
        description="Execute in background",
        prompt="Make the bounded change.",
        subagent_type="executor",
        tool_call_id="call-background",
        container="execution",
        delivery_cycle_index=1,
    )

    assert run_calls == []
    assert len(dispatcher.jobs) == 1
    assert result.additional_kwargs.get("subagent_status") is None
    assert "accepted for background execution" in result.content
    assert [event["type"] for event in events] == ["task_started"]

    outcome = asyncio.run(dispatcher.jobs[0].execute())

    assert len(run_calls) == 1
    assert outcome.status == "completed"
    assert outcome.result == "background work completed"
    assert [handoff["status"] for handoff in handoffs] == ["started", "completed"]


def test_command_room_task_carries_work_package_facts_through_background_dispatch(monkeypatch, tmp_path):
    events = []

    class FakeDispatcher:
        def __init__(self):
            self.jobs = []

        async def dispatch(self, job):
            self.jobs.append(job)

    dispatcher = FakeDispatcher()

    _patch_audit(monkeypatch, [])
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)

    result = _run_task_tool(
        runtime=_runtime(
            workspace_path=str(tmp_path / "workspace"),
            agent_name="command-room",
            background_dispatcher=dispatcher,
        ),
        description="Discover package context",
        prompt="Inspect the bounded package context.",
        subagent_type="planner",
        tool_call_id="call-package-context",
        container="context",
        container_artifact="context-discovery",
        work_package_id="package-next",
    )

    assert result.additional_kwargs["work_package_id"] == "package-next"
    assert events[-1]["work_package_id"] == "package-next"
    assert dispatcher.jobs[0].work_package_id == "package-next"
    assert "/packages/package-next/00-context/discovery/" in dispatcher.jobs[0].container_artifact_path


def test_background_task_persists_complete_terminal_tool_message(monkeypatch, tmp_path):
    class FakeDispatcher:
        def __init__(self):
            self.jobs = []

        async def dispatch(self, job):
            self.jobs.append(job)

    dispatcher = FakeDispatcher()
    store = MemoryRunEventStore()
    journal = RunJournal(run_id="run-1", thread_id="thread-1", event_store=store)

    async def run(prompt, **_kwargs):
        match = re.search(r"Write your complete natural-language handoff to: (.+)", prompt)
        assert match is not None
        Path(match.group(1)).write_text("# Executed\n", encoding="utf-8")
        return "Complete child result without truncation"

    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _event: None)
    monkeypatch.setattr(task_tool_module, "run_codex_cli_task", run)
    runtime = _runtime(
        workspace_path=str(tmp_path / "workspace"),
        agent_name="command-room",
        background_dispatcher=dispatcher,
    )
    runtime.context["__run_journal"] = journal

    _run_task_tool(
        runtime=runtime,
        description="Execute in background",
        prompt="Make the bounded change.",
        subagent_type="executor",
        tool_call_id="call-background-result",
        container="execution",
        delivery_cycle_index=1,
    )

    outcome = asyncio.run(dispatcher.jobs[0].execute())
    messages = asyncio.run(store.list_messages("thread-1", limit=100))
    terminal_messages = [row["content"] for row in messages if row["content"].get("type") == "tool" and row["content"].get("tool_call_id") == "call-background-result"]

    assert outcome.result == "Complete child result without truncation"
    assert len(terminal_messages) == 1
    terminal_message = terminal_messages[0]
    assert terminal_message["content"] == "Complete child result without truncation"
    assert terminal_message["name"] == "task"
    metadata = terminal_message["additional_kwargs"]
    assert metadata["subagent_status"] == "completed"
    assert metadata["command_room_container"] == "execution"
    assert Path(metadata["container_artifact_path"]).parent == (tmp_path / "workspace" / "command-room-loop" / "thread-1" / "03-delivery" / "cycle-01" / "execution")
    assert metadata["container_artifact_kind"] == "execution"
    assert metadata["delivery_cycle_index"] == 1
    assert metadata["container_artifact_written"] is True


def test_failed_background_task_persists_redacted_terminal_tool_message(monkeypatch, tmp_path):
    class FakeDispatcher:
        def __init__(self):
            self.jobs = []

        async def dispatch(self, job):
            self.jobs.append(job)

    dispatcher = FakeDispatcher()
    store = MemoryRunEventStore()
    journal = RunJournal(run_id="run-1", thread_id="thread-1", event_store=store)

    async def run(_prompt, **_kwargs):
        raise RuntimeError("provider token=secret-value failed")

    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _event: None)
    monkeypatch.setattr(task_tool_module, "run_codex_cli_task", run)
    runtime = _runtime(
        workspace_path=str(tmp_path / "workspace"),
        agent_name="command-room",
        background_dispatcher=dispatcher,
    )
    runtime.context["__run_journal"] = journal

    _run_task_tool(
        runtime=runtime,
        description="Execute in background",
        prompt="Make the bounded change.",
        subagent_type="executor",
        tool_call_id="call-background-failure",
        container="execution",
        delivery_cycle_index=1,
    )

    outcome = asyncio.run(dispatcher.jobs[0].execute())
    messages = asyncio.run(store.list_messages("thread-1", limit=100))
    terminal_messages = [row["content"] for row in messages if row["content"].get("type") == "tool" and row["content"].get("tool_call_id") == "call-background-failure"]

    assert outcome.status == "failed"
    assert len(terminal_messages) == 1
    terminal_message = terminal_messages[0]
    assert terminal_message["content"] == "Task failed. Error: provider [redacted] failed"
    assert "secret-value" not in terminal_message["content"]
    assert terminal_message["additional_kwargs"]["subagent_status"] == "failed"


def test_failed_background_task_reports_when_its_assigned_artifact_changed(monkeypatch, tmp_path):
    handoffs = []

    class FakeDispatcher:
        def __init__(self):
            self.jobs = []

        async def dispatch(self, job):
            self.jobs.append(job)

    dispatcher = FakeDispatcher()

    async def run(prompt, **_kwargs):
        match = re.search(r"Write your complete natural-language handoff to: (.+)", prompt)
        assert match is not None
        Path(match.group(1)).write_text("# Complete angle before transport failure\n", encoding="utf-8")
        raise RuntimeError("provider connection failed after output")

    _patch_audit(monkeypatch, handoffs)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _event: None)
    monkeypatch.setattr(task_tool_module, "run_codex_cli_task", run)

    _run_task_tool(
        runtime=_runtime(
            workspace_path=str(tmp_path / "workspace"),
            agent_name="command-room",
            background_dispatcher=dispatcher,
        ),
        description="Opposition",
        prompt="Write the contrary technical angle.",
        subagent_type="technical-opposition",
        tool_call_id="call-opposition",
        container="technical-design",
        container_artifact="technical-opposition",
    )

    outcome = asyncio.run(dispatcher.jobs[0].execute())

    assert outcome.status == "failed"
    assert outcome.container_artifact_written is True
    assert handoffs[-1]["status"] == "failed"


def test_command_room_execution_and_review_share_workspace_across_sequential_runs(monkeypatch, tmp_path):
    handoffs = []
    calls = []

    class FakeDispatcher:
        def __init__(self):
            self.jobs = []

        async def dispatch(self, job):
            self.jobs.append(job)

    dispatcher = FakeDispatcher()

    async def run(prompt, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        match = re.search(r"Write your complete natural-language handoff to: (.+)", prompt)
        assert match is not None
        Path(match.group(1)).write_text("# Completed handoff\n", encoding="utf-8")
        return "done"

    _patch_audit(monkeypatch, handoffs)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _event: None)
    monkeypatch.setattr(task_tool_module, "run_codex_cli_task", run)
    workspace = str(tmp_path / "workspace")

    execution = _run_task_tool(
        runtime=_runtime(
            workspace_path=workspace,
            agent_name="command-room",
            background_dispatcher=dispatcher,
            run_id="run-execution",
        ),
        description="Execution 1",
        prompt="Implement.",
        subagent_type="executor",
        tool_call_id="call-execution",
        container="execution",
        delivery_cycle_index=1,
    )
    assert execution.additional_kwargs["background_task"] is True
    asyncio.run(dispatcher.jobs.pop(0).execute())
    assert calls[0]["timeout_seconds"] == 3600

    review = _run_task_tool(
        runtime=_runtime(
            workspace_path=workspace,
            agent_name="command-room",
            background_dispatcher=dispatcher,
            run_id="run-review",
        ),
        description="Review 1",
        prompt="Review the actual result.",
        subagent_type="evidence",
        tool_call_id="call-review",
        container="review",
        delivery_cycle_index=1,
    )

    assert review.additional_kwargs["background_task"] is True
    assert dispatcher.jobs[0].source_run_id == "run-review"
    assert dispatcher.jobs[0].command_room_container == "review"
    asyncio.run(dispatcher.jobs.pop(0).execute())
    assert calls[1]["timeout_seconds"] == 900
    assert "Verify only whether the bounded execution landed as requested" in calls[1]["prompt"]
    assert "Do not implement, repair, refactor, or broaden the review" in calls[1]["prompt"]
    assert "Stop as soon as the landing judgment is supported" in calls[1]["prompt"]


def test_command_room_task_runs_in_background_without_workflow_labels(monkeypatch, tmp_path):
    events = []
    handoffs = []
    run_calls = []

    class FakeDispatcher:
        def __init__(self):
            self.jobs = []

        async def dispatch(self, job):
            self.jobs.append(job)

    dispatcher = FakeDispatcher()

    async def run(*_args, **_kwargs):
        run_calls.append("called")
        return "Free task result."

    _patch_audit(monkeypatch, handoffs)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module, "run_codex_cli_task", run)

    result = _run_task_tool(
        runtime=_runtime(
            workspace_path=str(tmp_path / "workspace"),
            agent_name="command-room",
            background_dispatcher=dispatcher,
        ),
        description="Inspect one issue",
        prompt="Inspect the issue and return the complete result.",
        subagent_type="general-purpose",
        tool_call_id="call-free-task",
    )

    assert result.additional_kwargs["background_task"] is True
    assert "command_room_container" not in result.additional_kwargs
    assert dispatcher.jobs[0].command_room_container is None
    assert dispatcher.jobs[0].delivery_cycle_index is None
    outcome = asyncio.run(dispatcher.jobs.pop(0).execute())
    assert outcome.result == "Free task result."
    assert run_calls == ["called"]
    assert [event["type"] for event in events] == ["task_started"]
    assert [handoff["status"] for handoff in handoffs] == ["started", "completed"]


def test_command_room_labels_do_not_block_review_without_execution_artifact(monkeypatch, tmp_path):
    events = []
    handoffs = []
    run_calls = []

    async def run(*_args, **_kwargs):
        run_calls.append("called")
        return "Natural result without writing the assigned file."

    _patch_audit(monkeypatch, handoffs)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module, "run_codex_cli_task", run)
    runtime = _runtime(workspace_path=str(tmp_path / "workspace"), agent_name="command-room")

    execution = _run_task_tool(
        runtime=runtime,
        description="Execute without artifact",
        prompt="Return only.",
        subagent_type="executor",
        tool_call_id="call-execution-no-artifact",
        container="execution",
        delivery_cycle_index=1,
    )
    review = _run_task_tool(
        runtime=runtime,
        description="Independent review",
        prompt="Inspect whatever result is available.",
        subagent_type="executor-checker",
        tool_call_id="call-blocked-review",
        container="review",
        delivery_cycle_index=1,
    )

    assert execution.content == "Natural result without writing the assigned file."
    assert execution.additional_kwargs["subagent_status"] == "completed"
    assert execution.additional_kwargs["container_artifact_written"] is False
    assert review.content == "Natural result without writing the assigned file."
    assert review.additional_kwargs["subagent_status"] == "completed"
    assert review.additional_kwargs["container_artifact_written"] is False
    assert run_calls == ["called", "called"]


def test_command_room_task_tool_carries_execution_review_loop_handoffs(monkeypatch, tmp_path):
    events = []
    handoffs = []
    workspace = tmp_path / "thread" / "user-data" / "workspace"

    async def run(prompt, **_kwargs):
        match = re.search(r"Write your complete natural-language handoff to: (.+)", prompt)
        assert match is not None
        output_path = Path(match.group(1))
        output_path.write_text("# AI-authored handoff\n\nNatural-language result.\n", encoding="utf-8")
        return "Natural result returned unchanged."

    _patch_audit(monkeypatch, handoffs)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module, "run_codex_cli_task", run)
    runtime = _runtime(workspace_path=str(workspace), agent_name="command-room")

    execution_one = _run_task_tool(
        runtime=runtime,
        description="Work",
        prompt="Implement and report cycle one.",
        subagent_type="executor",
        tool_call_id="call-execution-one",
        container="execution",
        delivery_cycle_index=1,
    )
    review_one = _run_task_tool(
        runtime=runtime,
        description="Review",
        prompt="Independently review cycle one.",
        subagent_type="executor-checker",
        tool_call_id="call-review-one",
        container="review",
        delivery_cycle_index=1,
    )
    execution_two = _run_task_tool(
        runtime=runtime,
        description="Rework",
        prompt="Read cycle-one findings and correct the accepted issues.",
        subagent_type="executor",
        tool_call_id="call-execution-two",
        container="execution",
        delivery_cycle_index=2,
    )
    review_two = _run_task_tool(
        runtime=runtime,
        description="Review rework",
        prompt="Independently review cycle two.",
        subagent_type="executor-checker",
        tool_call_id="call-review-two",
        container="review",
        delivery_cycle_index=2,
    )

    assert [message.content for message in (execution_one, review_one, execution_two, review_two)] == [
        "Natural result returned unchanged.",
    ] * 4
    assert [message.additional_kwargs["command_room_container"] for message in (execution_one, review_one, execution_two, review_two)] == [
        "execution",
        "review",
        "execution",
        "review",
    ]
    assert execution_one.additional_kwargs["delivery_cycle_index"] == 1
    assert review_one.additional_kwargs["container_artifact_kind"] == "findings"
    assert execution_two.additional_kwargs["delivery_cycle_index"] == 2
    assert all(message.additional_kwargs["container_artifact_written"] is True for message in (execution_one, review_one, execution_two, review_two))
    assert [event["command_room_container"] for event in events if event["type"] == "task_completed"] == [
        "execution",
        "review",
        "execution",
        "review",
    ]
    assert [handoff["status"] for handoff in handoffs] == ["started", "completed"] * 4


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
