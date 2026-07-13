"""Tests for the direct Codex CLI task runner."""

import asyncio
import signal
from pathlib import Path

import pytest

from deerflow.subagents.codex_cli import CodexCliError, CodexCliTimeoutError, run_codex_cli_task


class _Stream:
    def __init__(self, content: bytes):
        self.content = content

    async def read(self) -> bytes:
        return self.content


class _Process:
    def __init__(self, *, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self.stdout = _Stream(stdout)
        self.stderr = _Stream(stderr)
        self.pid = None

    async def wait(self) -> int:
        return self.returncode


class _InterruptibleProcess(_Process):
    def __init__(self):
        super().__init__(returncode=None)
        self.signals = []
        self.finished = asyncio.Event()

    async def wait(self) -> int:
        await self.finished.wait()
        return self.returncode

    def send_signal(self, value) -> None:
        self.signals.append(value)
        self.returncode = -value
        self.finished.set()


@pytest.mark.asyncio
async def test_run_codex_cli_task_uses_one_shot_workspace_write_command(monkeypatch, tmp_path):
    captured = {}

    async def create_process(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        output_path = Path(args[args.index("--output-last-message") + 1])
        output_path.write_text("finished task", encoding="utf-8")
        return _Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    result = await run_codex_cli_task("do the task", workspace_path=tmp_path, timeout_seconds=60)

    assert result.result == "finished task"
    args = captured["args"]
    assert args[:4] == ("codex", "exec", "--json", "--ephemeral")
    assert args[args.index("--sandbox") + 1] == "workspace-write"
    assert args[args.index("--cd") + 1] == str(tmp_path.resolve())
    assert args[-1] == "do the task"
    assert captured["kwargs"]["start_new_session"] is True


@pytest.mark.asyncio
async def test_run_codex_cli_task_passes_configured_model_and_reasoning_effort(monkeypatch, tmp_path):
    captured = {}

    async def create_process(*args, **_kwargs):
        captured["args"] = args
        output_path = Path(args[args.index("--output-last-message") + 1])
        output_path.write_text("finished task", encoding="utf-8")
        return _Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    await run_codex_cli_task(
        "do the task",
        workspace_path=tmp_path,
        timeout_seconds=60,
        model="gpt-5.6-terra",
        reasoning_effort="xhigh",
    )

    args = captured["args"]
    assert args[args.index("--model") + 1] == "gpt-5.6-terra"
    assert args[args.index("--config") + 1] == 'model_reasoning_effort="xhigh"'


@pytest.mark.asyncio
async def test_run_codex_cli_task_reports_nonzero_exit(monkeypatch, tmp_path):
    async def create_process(*_args, **_kwargs):
        return _Process(returncode=1, stderr=b"authentication failed")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    with pytest.raises(CodexCliError, match="authentication failed"):
        await run_codex_cli_task("do the task", workspace_path=tmp_path, timeout_seconds=60)


@pytest.mark.asyncio
async def test_run_codex_cli_task_reports_start_failure(monkeypatch, tmp_path):
    async def create_process(*_args, **_kwargs):
        raise PermissionError("execution denied")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    with pytest.raises(CodexCliError, match="could not be started: execution denied"):
        await run_codex_cli_task("do the task", workspace_path=tmp_path, timeout_seconds=60)


@pytest.mark.asyncio
async def test_run_codex_cli_task_requires_workspace(tmp_path):
    with pytest.raises(CodexCliError, match="No thread workspace"):
        await run_codex_cli_task("do the task", workspace_path=None, timeout_seconds=60)


@pytest.mark.asyncio
async def test_run_codex_cli_task_falls_back_to_json_final_message(monkeypatch, tmp_path):
    async def create_process(*_args, **_kwargs):
        return _Process(stdout=b'{"type":"item.completed","item":{"type":"agent_message","text":"final answer"}}\n')

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    result = await run_codex_cli_task("do the task", workspace_path=tmp_path, timeout_seconds=60)

    assert result.result == "final answer"


@pytest.mark.asyncio
async def test_run_codex_cli_task_interrupts_process_on_timeout(monkeypatch, tmp_path):
    process = _InterruptibleProcess()

    async def create_process(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    with pytest.raises(CodexCliTimeoutError):
        await run_codex_cli_task("do the task", workspace_path=tmp_path, timeout_seconds=0)

    assert process.signals == [signal.SIGINT]


@pytest.mark.asyncio
async def test_run_codex_cli_task_interrupts_process_on_cancellation(monkeypatch, tmp_path):
    process = _InterruptibleProcess()

    async def create_process(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    task = asyncio.create_task(run_codex_cli_task("do the task", workspace_path=tmp_path, timeout_seconds=60))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert process.signals == [signal.SIGINT]
