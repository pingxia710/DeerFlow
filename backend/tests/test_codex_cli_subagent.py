"""Tests for the one-shot Codex CLI transport."""

import asyncio
import os
import signal
import sys
from pathlib import Path

import pytest

from deerflow.subagents import codex_cli
from deerflow.subagents.codex_cli import run_codex_cli_task


class _Process:
    def __init__(self, *, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()
        self.terminated = False
        self.killed = False
        self.input = None
        self.stdin = _Stdin(self)
        self.finished = asyncio.Event()
        if returncode is not None:
            self.finished.set()

    async def wait(self) -> int:
        await self.finished.wait()
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15
        self.finished.set()

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self.finished.set()


class _Stdin:
    def __init__(self, process: _Process):
        self.process = process

    def write(self, value: bytes) -> None:
        self.process.input = value

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


@pytest.mark.asyncio
async def test_run_codex_cli_task_passes_prompt_and_returns_final_text(monkeypatch, tmp_path):
    captured = {}
    outputs_path = tmp_path / "outputs"
    outputs_path.mkdir()

    async def create_process(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        output_path = Path(args[args.index("--output-last-message") + 1])
        output_path.write_text("  finished task\n", encoding="utf-8")
        process = _Process()
        captured["process"] = process
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    result = await run_codex_cli_task(
        "do the task",
        workspace_path=tmp_path,
        timeout_seconds=60,
        additional_writable_paths=[outputs_path],
    )

    assert result == "  finished task\n"
    args = captured["args"]
    assert args[:3] == ("codex", "exec", "--ephemeral")
    assert "--json" not in args
    assert args[args.index("--sandbox") + 1] == "workspace-write"
    assert args[args.index("--cd") + 1] == str(tmp_path.resolve())
    assert args[args.index("--add-dir") + 1] == str(outputs_path.resolve())
    configs = [args[index + 1] for index, value in enumerate(args) if value == "--config"]
    assert 'shell_environment_policy.inherit="core"' in configs
    assert "shell_environment_policy.ignore_default_excludes=false" in configs
    assert "shell_environment_policy.experimental_use_profile=false" in configs
    assert args[-1] == "-"
    assert captured["process"].input == b"do the task"
    assert captured["kwargs"]["stdin"] == asyncio.subprocess.PIPE
    assert captured["kwargs"]["stdout"] == asyncio.subprocess.DEVNULL
    assert captured["kwargs"]["stderr"] == asyncio.subprocess.PIPE
    assert captured["kwargs"]["start_new_session"] is True
    assert captured["kwargs"]["env"]["PATH"]


@pytest.mark.asyncio
async def test_run_codex_cli_task_reads_leading_dash_and_large_prompt_from_stdin(monkeypatch, tmp_path):
    captured = {}
    prompt = "--not-a-cli-option\n" + ("完整自然语言结果" * 100_000)

    async def create_process(*args, **kwargs):
        output_path = Path(args[args.index("--output-last-message") + 1])
        output_path.write_text("finished", encoding="utf-8")
        process = _Process()
        captured.update(args=args, kwargs=kwargs, process=process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    assert await run_codex_cli_task(prompt, workspace_path=tmp_path, timeout_seconds=60) == "finished"
    assert captured["args"][-1] == "-"
    assert captured["process"].input == prompt.encode("utf-8")


@pytest.mark.asyncio
async def test_run_codex_cli_task_does_not_inherit_gateway_secrets(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setenv("DATABASE_URL", "postgres://private")
    monkeypatch.setenv("CHANNEL_ACCESS_TOKEN", "private-channel-token")
    monkeypatch.setenv("OPENAI_API_KEY", "private-model-key")
    monkeypatch.setenv("CODEX_API_KEY", "native-codex-key")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))

    async def create_process(*args, **kwargs):
        captured["env"] = kwargs["env"]
        output_path = Path(args[args.index("--output-last-message") + 1])
        output_path.write_text("finished", encoding="utf-8")
        return _Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    assert await run_codex_cli_task("do the task", workspace_path=tmp_path, timeout_seconds=60) == "finished"
    assert captured["env"]["CODEX_HOME"] == str(tmp_path / ".codex")
    assert captured["env"]["PATH"] == os.environ["PATH"]
    assert "DATABASE_URL" not in captured["env"]
    assert "CHANNEL_ACCESS_TOKEN" not in captured["env"]
    assert captured["env"]["OPENAI_API_KEY"] == "private-model-key"
    assert captured["env"]["CODEX_API_KEY"] == "native-codex-key"


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
        sandbox_mode="danger-full-access",
    )

    args = captured["args"]
    assert args[args.index("--model") + 1] == "gpt-5.6-terra"
    configs = [args[index + 1] for index, value in enumerate(args) if value == "--config"]
    assert 'model_reasoning_effort="xhigh"' in configs
    assert args[args.index("--sandbox") + 1] == "danger-full-access"


@pytest.mark.asyncio
async def test_run_codex_cli_task_reports_nonzero_exit(monkeypatch, tmp_path):
    async def create_process(*_args, **_kwargs):
        return _Process(returncode=1, stderr=b"authentication failed")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    with pytest.raises(RuntimeError, match="authentication failed"):
        await run_codex_cli_task("do the task", workspace_path=tmp_path, timeout_seconds=60)


@pytest.mark.asyncio
async def test_bounded_stream_keeps_only_tail():
    stream = asyncio.StreamReader()
    stream.feed_data(b"x" * (codex_cli._MAX_ERROR_BYTES * 3) + b"tail")
    stream.feed_eof()

    result = await codex_cli._read_bounded_stream(stream)

    assert len(result) == codex_cli._MAX_ERROR_BYTES
    assert result.endswith(b"tail")


@pytest.mark.asyncio
async def test_run_codex_cli_task_cleans_up_after_communication_failure(monkeypatch, tmp_path):
    process = _Process(returncode=None)

    async def create_process(*_args, **_kwargs):
        return process

    async def fail_communication(_process, _prompt):
        raise OSError("pipe failed")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(codex_cli, "_feed_prompt_and_wait", fail_communication)

    with pytest.raises(RuntimeError, match="communication failed: pipe failed"):
        await run_codex_cli_task("do the task", workspace_path=tmp_path, timeout_seconds=60)

    assert process.terminated is True


@pytest.mark.asyncio
async def test_run_codex_cli_task_reports_start_failure(monkeypatch, tmp_path):
    async def create_process(*_args, **_kwargs):
        raise PermissionError("execution denied")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    with pytest.raises(RuntimeError, match="could not be started: execution denied"):
        await run_codex_cli_task("do the task", workspace_path=tmp_path, timeout_seconds=60)


@pytest.mark.asyncio
async def test_run_codex_cli_task_requires_workspace():
    with pytest.raises(RuntimeError, match="No thread workspace"):
        await run_codex_cli_task("do the task", workspace_path=None, timeout_seconds=60)


@pytest.mark.asyncio
async def test_run_codex_cli_task_requires_final_text(monkeypatch, tmp_path):
    async def create_process(*_args, **_kwargs):
        return _Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    with pytest.raises(RuntimeError, match="without a final message"):
        await run_codex_cli_task("do the task", workspace_path=tmp_path, timeout_seconds=60)


@pytest.mark.asyncio
async def test_run_codex_cli_task_wraps_final_message_read_failure(monkeypatch, tmp_path):
    async def create_process(*_args, **_kwargs):
        return _Process()

    def fail_read_text(_path, **_kwargs):
        raise PermissionError("read denied")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(Path, "read_text", fail_read_text)

    with pytest.raises(RuntimeError, match="final message could not be read"):
        await run_codex_cli_task("do the task", workspace_path=tmp_path, timeout_seconds=60)


@pytest.mark.asyncio
async def test_run_codex_cli_task_terminates_process_on_timeout(monkeypatch, tmp_path):
    process = _Process(returncode=None)

    async def create_process(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    with pytest.raises(TimeoutError, match="timed out"):
        await run_codex_cli_task("do the task", workspace_path=tmp_path, timeout_seconds=0)

    assert process.terminated is True


@pytest.mark.asyncio
async def test_stop_process_kills_remaining_group_after_parent_exits(monkeypatch):
    process = _Process(returncode=0)
    process.pid = 12345
    signals = []
    group_waits = iter([False, True])

    def signal_group(_process, sig):
        signals.append(sig)
        return True

    async def wait_for_group(_process_group_id, _timeout):
        return next(group_waits)

    monkeypatch.setattr(codex_cli, "_signal_process_group", signal_group)
    monkeypatch.setattr(codex_cli, "_wait_for_process_group_exit", wait_for_group)

    await codex_cli._stop_process(process)

    assert signals == [signal.SIGTERM, signal.SIGKILL]


@pytest.mark.asyncio
async def test_run_codex_cli_task_terminates_process_on_cancellation(monkeypatch, tmp_path):
    process = _Process(returncode=None)

    async def create_process(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    task = asyncio.create_task(run_codex_cli_task("do the task", workspace_path=tmp_path, timeout_seconds=60))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert process.terminated is True


@pytest.mark.asyncio
async def test_repeated_cancellation_force_kills_process_that_ignores_sigterm(monkeypatch, tmp_path):
    ready_path = tmp_path / "ready"
    term_path = tmp_path / "term"
    binary_path = tmp_path / "fake-codex"
    binary_path.write_text(
        f"""#!{sys.executable}
import os
import signal
import time
from pathlib import Path

term_path = Path({str(term_path)!r})

def handle_term(_signum, _frame):
    term_path.write_text("ignored", encoding="utf-8")

signal.signal(signal.SIGTERM, handle_term)
Path({str(ready_path)!r}).write_text(str(os.getpid()), encoding="utf-8")
while True:
    time.sleep(0.05)
""",
        encoding="utf-8",
    )
    binary_path.chmod(0o755)
    monkeypatch.setattr(codex_cli, "_TERMINATE_GRACE_SECONDS", 0.2)

    task = asyncio.create_task(
        run_codex_cli_task(
            "do the task",
            workspace_path=tmp_path,
            timeout_seconds=60,
            binary=str(binary_path),
        )
    )
    for _ in range(100):
        if ready_path.exists():
            break
        await asyncio.sleep(0.01)
    assert ready_path.exists()
    pid = int(ready_path.read_text(encoding="utf-8"))

    task.cancel()
    for _ in range(100):
        if term_path.exists():
            break
        await asyncio.sleep(0.01)
    assert term_path.exists()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
