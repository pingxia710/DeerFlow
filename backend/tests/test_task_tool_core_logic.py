"""Task-tool contract tests for direct Codex CLI delegation."""

from __future__ import annotations

import asyncio
import importlib
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
    inputs_path: str | None = None,
    outputs_path: str | None = None,
    app_config=None,
    round_id: str | None = None,
    agent_name: str | None = None,
    background_dispatcher=None,
    goal_cell_transport: bool = False,
    goal_cell_input_capsule: bool = False,
    run_id: str = "run-1",
):
    app_config = app_config or SimpleNamespace(subagents=SimpleNamespace(timeout_seconds=timeout_seconds))
    thread_data = {"workspace_path": workspace_path}
    if uploads_path is not None:
        thread_data["uploads_path"] = uploads_path
    if inputs_path is not None:
        thread_data["inputs_path"] = inputs_path
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
    if goal_cell_transport:
        context["__nextos_goal_cell_transport"] = True
    if goal_cell_input_capsule:
        context["__nextos_goal_cell_input_capsule"] = True
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


def test_task_tool_runs_codex_cli_and_preserves_complete_result(monkeypatch):
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
        return "complete natural-language result"

    _patch_audit(monkeypatch, handoffs)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module, "run_codex_cli_task", run)

    result = _run_task_tool(
        runtime=_runtime(timeout_seconds=3600),
        description="Inspect backend",
        prompt="Inspect the backend and return every relevant fact.",
        subagent_type="fact-finder",
        tool_call_id="call-1",
    )

    assert result.content == "complete natural-language result"
    assert captured["workspace_path"] == str(Path("/tmp/deerflow-thread").resolve())
    assert captured["timeout_seconds"] == 3600
    assert captured["model"] is None
    assert captured["reasoning_effort"] is None
    assert captured["sandbox_mode"] == "workspace-write"
    assert captured["additional_writable_paths"] == []
    assert "# Professional role: fact-finder" in captured["prompt"]
    assert "# Command Room task\n\nInspect the backend and return every relevant fact." in captured["prompt"]
    assert "read the complete AGENTS.md instruction chain" in captured["prompt"]
    assert f"- Workspace: {Path('/tmp/deerflow-thread').resolve()}" in captured["prompt"]
    assert [event["type"] for event in events] == ["task_started", "task_completed"]
    assert [handoff["status"] for handoff in handoffs] == ["started", "completed"]


def test_task_tool_creates_fresh_lazy_workspace_before_codex(monkeypatch, tmp_path):
    workspace = tmp_path / "fresh-thread" / "user-data" / "workspace"

    async def run(_prompt, *, workspace_path, **_kwargs):
        assert workspace_path == str(workspace)
        assert Path(workspace_path).is_dir()
        return "done"

    _patch_audit(monkeypatch, [])
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _event: None)
    monkeypatch.setattr(task_tool_module, "run_codex_cli_task", run)

    result = _run_task_tool(
        runtime=_runtime(workspace_path=str(workspace)),
        description="Fresh task",
        prompt="Run immediately on a fresh thread.",
        subagent_type="executor",
        tool_call_id="call-fresh",
    )

    assert result.content == "done"
    assert workspace.is_dir()


def test_goal_cell_task_uses_readonly_capsule_and_never_inherits_host_access(monkeypatch, tmp_path):
    captured = {}
    inputs = tmp_path / "inputs"
    inputs.mkdir()

    async def run(prompt, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)
        return "done"

    _patch_audit(monkeypatch, [])
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _event: None)
    monkeypatch.setattr(task_tool_module, "run_codex_cli_task", run)
    monkeypatch.setattr(task_tool_module, "is_unrestricted_host_access_allowed", lambda _config: True)

    result = _run_task_tool(
        runtime=_runtime(
            workspace_path=str(tmp_path / "workspace"),
            inputs_path=str(inputs),
            goal_cell_transport=True,
            goal_cell_input_capsule=True,
        ),
        description="Use sealed evidence",
        prompt="Read the supplied material and return the full result.",
        subagent_type="fact-finder",
        tool_call_id="call-sealed-input",
    )

    assert result.content == "done"
    assert captured["sandbox_mode"] == "workspace-write"
    assert f"- Sealed input capsule (read-only): {inputs}" in captured["prompt"]


def test_command_room_task_dispatches_complete_prompt_in_background(monkeypatch, tmp_path):
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
        description="Inspect one issue",
        prompt="Inspect the issue and return the complete result.",
        subagent_type="fact-finder",
        tool_call_id="call-background",
    )

    assert run_calls == []
    assert len(dispatcher.jobs) == 1
    assert result.additional_kwargs == {
        "background_task": True,
        "background_task_id": "call-background",
    }
    assert "accepted for background execution" in result.content
    assert "End this turn" not in result.content
    assert [event["type"] for event in events] == ["task_started"]

    outcome = asyncio.run(dispatcher.jobs[0].execute())

    assert len(run_calls) == 1
    assert "Inspect the issue and return the complete result." in run_calls[0]
    assert outcome.status == "completed"
    assert outcome.result == "background work completed"
    assert [handoff["status"] for handoff in handoffs] == ["started", "completed"]


def test_background_task_persists_complete_terminal_tool_message(monkeypatch, tmp_path):
    class FakeDispatcher:
        def __init__(self):
            self.jobs = []

        async def dispatch(self, job):
            self.jobs.append(job)

    dispatcher = FakeDispatcher()
    store = MemoryRunEventStore()
    journal = RunJournal(run_id="run-1", thread_id="thread-1", event_store=store)

    async def run(_prompt, **_kwargs):
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
    )

    outcome = asyncio.run(dispatcher.jobs[0].execute())
    messages = asyncio.run(store.list_messages("thread-1", limit=100))
    terminal_messages = [row["content"] for row in messages if row["content"].get("type") == "tool" and row["content"].get("tool_call_id") == "call-background-result"]

    assert outcome.result == "Complete child result without truncation"
    assert len(terminal_messages) == 1
    assert terminal_messages[0]["content"] == "Complete child result without truncation"
    assert terminal_messages[0]["name"] == "task"
    assert terminal_messages[0]["additional_kwargs"] == {"subagent_status": "completed"}


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
    )

    outcome = asyncio.run(dispatcher.jobs[0].execute())
    messages = asyncio.run(store.list_messages("thread-1", limit=100))
    terminal_messages = [row["content"] for row in messages if row["content"].get("type") == "tool" and row["content"].get("tool_call_id") == "call-background-failure"]

    assert outcome.status == "failed"
    assert terminal_messages[0]["content"] == "Task failed. Error: provider [redacted] failed"
    assert "secret-value" not in terminal_messages[0]["content"]
    assert terminal_messages[0]["additional_kwargs"] == {"subagent_status": "failed"}


def test_background_dispatch_failure_is_recorded(monkeypatch, tmp_path):
    events = []
    handoffs = []

    class FailingDispatcher:
        async def dispatch(self, _job):
            raise RuntimeError("queue unavailable")

    _patch_audit(monkeypatch, handoffs)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)

    result = _run_task_tool(
        runtime=_runtime(
            workspace_path=str(tmp_path / "workspace"),
            agent_name="command-room",
            background_dispatcher=FailingDispatcher(),
        ),
        description="Background task",
        prompt="Return the complete result.",
        subagent_type="executor",
        tool_call_id="call-dispatch-failed",
    )

    assert result.additional_kwargs == {"subagent_status": "failed"}
    assert "queue unavailable" in result.content
    assert [event["type"] for event in events] == ["task_started", "task_failed"]
    assert [handoff["status"] for handoff in handoffs] == ["started", "failed"]


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
        subagent_type="executor",
        tool_call_id="call-workspace-failed",
    )

    assert result.additional_kwargs["subagent_status"] == "failed"
    assert [event["type"] for event in events] == ["task_failed"]
    assert [handoff["status"] for handoff in handoffs] == ["failed"]


def test_task_tool_combines_selected_professional_role_prompt(monkeypatch, tmp_path):
    captured = {}
    subagents = SimpleNamespace(
        timeout_seconds=3600,
        reasoning_effort="xhigh",
        custom_agents={
            "contract-specialist": SimpleNamespace(
                description="Analyzes contracts.",
                system_prompt="Analyze the contract independently.",
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
        return "analysis complete"

    _patch_audit(monkeypatch, [])
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda _event: None)
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
        description="Analyze contract",
        prompt="Inspect the API response shape.",
        subagent_type="contract-specialist",
        tool_call_id="call-role",
    )

    assert result.content == "analysis complete"
    assert captured["prompt"].startswith("# Professional role: contract-specialist\n\nAnalyze the contract independently.\n\n# Command Room task\n\nInspect the API response shape.")
    assert f"- Workspace: {workspace}" in captured["prompt"]
    assert f"- Uploaded files (read): {uploads}" in captured["prompt"]
    assert f"- Output artifacts (write): {outputs}" in captured["prompt"]
    assert captured["model"] == "gpt-5.6-terra"
    assert captured["reasoning_effort"] == "xhigh"
    assert captured["additional_writable_paths"] == [str(outputs)]
    assert workspace.is_dir()
    assert uploads.is_dir()
    assert outputs.is_dir()


def test_task_model_options_resolve_role_model_and_reasoning_effort():
    subagents = SimpleNamespace(
        reasoning_effort="xhigh",
        get_model_for=lambda name: "gpt-5.6" if name == "planner" else "gpt-5.6-terra",
        get_reasoning_effort_for=lambda name: "max" if name == "planner" else "xhigh",
    )
    app_config = SimpleNamespace(
        subagents=subagents,
        get_model_config=lambda name: SimpleNamespace(model="gpt-5.6-sol") if name == "gpt-5.6" else SimpleNamespace(model=name),
    )

    assert task_tool_module._task_model_options(app_config, "planner") == ("gpt-5.6-sol", "max")
    assert task_tool_module._task_model_options(app_config, "executor") == ("gpt-5.6-terra", "xhigh")


def test_task_model_options_prefer_user_role_assignment():
    from deerflow.config.role_assignments import RoleAssignment

    app_config = SimpleNamespace(
        subagents=SimpleNamespace(
            get_model_for=lambda _name: "gpt-5.6-terra",
            get_reasoning_effort_for=lambda _name: "xhigh",
        ),
        get_model_config=lambda name: SimpleNamespace(model="gpt-5.6-sol") if name == "gpt-5.6" else None,
    )

    assignment = RoleAssignment(model="gpt-5.6", reasoning_effort="max")

    assert task_tool_module._task_model_options(app_config, "planner", assignment) == ("gpt-5.6-sol", "max")


def test_task_worker_prompt_falls_back_to_role_description_and_keeps_unknown_label():
    general_prompt = task_tool_module._task_worker_prompt(
        None,
        "executor",
        "Implement the change.",
        {"workspace_path": "/tmp/workspace"},
    )
    unknown_prompt = task_tool_module._task_worker_prompt(
        None,
        "specialist-not-yet-registered",
        "Inspect the target.",
        {"workspace_path": "/tmp/workspace"},
    )

    assert "# Professional role: executor" in general_prompt
    assert "# Professional role: specialist-not-yet-registered" in unknown_prompt
    assert 'Work as the professional role "specialist-not-yet-registered"' in unknown_prompt
    assert "read the complete AGENTS.md instruction chain" in unknown_prompt


def test_task_worker_prompt_includes_the_selected_role_package(monkeypatch, tmp_path):
    role_dir = tmp_path / "command-room-executor"
    role_dir.mkdir()
    skill_file = role_dir / "SKILL.md"
    skill_file.write_text("# Executor method\n\nReturn observed facts.", encoding="utf-8")
    (role_dir / "AGENTS.md").write_text("# Executor charter\n\nDo not decide plan completion.", encoding="utf-8")
    storage = SimpleNamespace(
        get_custom_skill_file=lambda _name: skill_file,
        get_custom_skill_dir=lambda _name: role_dir,
    )
    monkeypatch.setattr(task_tool_module, "get_or_new_skill_storage", lambda *, app_config: storage)

    prompt = task_tool_module._task_worker_prompt(
        SimpleNamespace(skills=object()),
        "executor",
        "Implement the bounded change.",
        {"workspace_path": "/tmp/workspace"},
    )

    assert "# Role governance: executor" in prompt
    assert "# Executor charter" in prompt
    assert "# Executor method" in prompt


def test_task_worker_prompt_does_not_block_when_role_package_is_unavailable(monkeypatch, tmp_path):
    storage = SimpleNamespace(
        get_custom_skill_file=lambda _name: tmp_path / "missing" / "SKILL.md",
        get_custom_skill_dir=lambda _name: tmp_path / "missing",
    )
    monkeypatch.setattr(task_tool_module, "get_or_new_skill_storage", lambda *, app_config: storage)

    prompt = task_tool_module._task_worker_prompt(
        SimpleNamespace(skills=object()),
        "executor",
        "Implement the bounded change.",
        {"workspace_path": "/tmp/workspace"},
    )

    assert "# Professional role: executor" in prompt
    assert "# Role governance: executor" not in prompt


def test_task_tool_stamps_round_identity_as_a_fact(monkeypatch, tmp_path):
    events = []

    async def run(*_args, **_kwargs):
        return "round result"

    _patch_audit(monkeypatch, [])
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module, "run_codex_cli_task", run)

    result = _run_task_tool(
        runtime=_runtime(workspace_path=str(tmp_path / "workspace"), round_id="round-1"),
        description="Grouped task",
        prompt="Return the result.",
        subagent_type="executor",
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
        description="Inspect backend",
        prompt="Inspect the backend.",
        subagent_type="fact-finder",
        tool_call_id="call-2",
    )

    expected_prefix = "Task timed out" if expected_status == "timed_out" else "Task failed"
    assert result.content == f"{expected_prefix}. Error: {error}"
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
            description="Inspect backend",
            prompt="Inspect the backend.",
            subagent_type="fact-finder",
            tool_call_id="call-3",
        )

    assert events[-1]["type"] == "task_cancelled"
    assert handoffs[-1]["status"] == "cancelled"
