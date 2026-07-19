"""Transport one natural-language task through one Codex CLI process."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from deerflow.utils.cancellation import await_task_through_repeated_cancellation

_TERMINATE_GRACE_SECONDS = 2.0
_FORCE_KILL_GRACE_SECONDS = 1.0
_PROCESS_GROUP_POLL_SECONDS = 0.05
_MAX_ERROR_BYTES = 16_000
_MAX_ERROR_CHARS = 4_000
_CODEX_ENV_KEYS = frozenset(
    {
        "ALL_PROXY",
        "CODEX_ACCESS_TOKEN",
        "CODEX_API_KEY",
        "CODEX_AUTH_PATH",
        "CODEX_HOME",
        "HOME",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOGNAME",
        "NO_PROXY",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_ORGANIZATION",
        "OPENAI_PROJECT",
        "PATH",
        "SHELL",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TEMP",
        "TMP",
        "TMPDIR",
        "TZ",
        "USER",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "all_proxy",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)

CodexSandboxMode = Literal["read-only", "workspace-write", "danger-full-access"]


def _codex_subprocess_env(source: dict[str, str] | None = None) -> dict[str, str]:
    """Expose only process/runtime settings, never the Gateway's business secrets."""
    source = dict(os.environ) if source is None else source
    env = {key: value for key, value in source.items() if key in _CODEX_ENV_KEYS or key.startswith("LC_")}
    env.setdefault("PATH", os.defpath)
    return env


def _workspace_path(value: str | Path | None) -> Path:
    if not value:
        raise RuntimeError("No thread workspace is available for the Codex CLI task.")
    workspace = Path(value).expanduser().resolve()
    if not workspace.is_dir():
        raise RuntimeError(f"Codex CLI workspace does not exist: {workspace}")
    return workspace


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


async def _wait_for_process_group_exit(process_group_id: int, timeout: float) -> bool:
    deadline = asyncio.get_running_loop().time() + max(0.0, timeout)
    while _process_group_exists(process_group_id):
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(_PROCESS_GROUP_POLL_SECONDS, remaining))
    return True


def _signal_process_group(process: asyncio.subprocess.Process, sig: signal.Signals) -> bool:
    try:
        os.killpg(process.pid, sig)
    except (AttributeError, OSError, TypeError):
        return False
    return True


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    process_group_id = getattr(process, "pid", None)
    group_signalled = isinstance(process_group_id, int) and _signal_process_group(process, signal.SIGTERM)
    if not group_signalled and process.returncode is None:
        process.terminate()

    deadline = asyncio.get_running_loop().time() + _TERMINATE_GRACE_SECONDS
    if process.returncode is None:
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        try:
            await asyncio.wait_for(process.wait(), timeout=remaining)
        except TimeoutError:
            pass

    group_exited = True
    if group_signalled:
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        group_exited = await _wait_for_process_group_exit(process_group_id, remaining)

    if group_exited and process.returncode is not None:
        return

    if group_signalled:
        _signal_process_group(process, signal.SIGKILL)
    elif process.returncode is None:
        process.kill()

    if process.returncode is None:
        await process.wait()
    if group_signalled:
        await _wait_for_process_group_exit(process_group_id, _FORCE_KILL_GRACE_SECONDS)


async def _finish_process_cleanup(
    process: asyncio.subprocess.Process,
    stderr_task: asyncio.Task[bytes],
) -> bytes:
    try:
        await _stop_process(process)
    finally:
        stderr_tail = await stderr_task
    return stderr_tail


async def _read_bounded_stream(stream: asyncio.StreamReader | None) -> bytes:
    """Drain a process stream while retaining only its bounded tail."""
    if stream is None:
        return b""
    tail = bytearray()
    while chunk := await stream.read(4096):
        tail.extend(chunk)
        if len(tail) > _MAX_ERROR_BYTES:
            del tail[: len(tail) - _MAX_ERROR_BYTES]
    return bytes(tail)


def _bounded_error_text(value: bytes) -> str:
    return value.decode("utf-8", errors="replace").strip()[-_MAX_ERROR_CHARS:]


async def _feed_prompt_and_wait(process: asyncio.subprocess.Process, prompt: str) -> None:
    if process.stdin is None:
        raise RuntimeError("Codex CLI stdin pipe was not created.")
    process.stdin.write(prompt.encode("utf-8"))
    await process.stdin.drain()
    process.stdin.close()
    if hasattr(process.stdin, "wait_closed"):
        await process.stdin.wait_closed()
    await process.wait()


async def run_codex_cli_task(
    prompt: str,
    *,
    workspace_path: str | Path | None,
    timeout_seconds: int,
    model: str | None = None,
    reasoning_effort: str | None = None,
    sandbox_mode: CodexSandboxMode = "workspace-write",
    additional_writable_paths: Sequence[str | Path] = (),
    binary: str = "codex",
) -> str:
    """Pass a prompt to Codex and return its complete final message."""
    workspace = _workspace_path(workspace_path)
    output_file = tempfile.NamedTemporaryFile(prefix="deerflow-codex-", suffix=".txt", delete=False)
    output_path = Path(output_file.name)
    output_file.close()

    try:
        command = [
            binary,
            "exec",
            "--ephemeral",
            "--sandbox",
            sandbox_mode,
            "--config",
            'shell_environment_policy.inherit="core"',
            "--config",
            "shell_environment_policy.ignore_default_excludes=false",
            "--config",
            "shell_environment_policy.experimental_use_profile=false",
        ]
        if model:
            command.extend(["--model", model])
        if reasoning_effort:
            command.extend(["--config", f"model_reasoning_effort={json.dumps(reasoning_effort)}"])
        for value in additional_writable_paths:
            writable_path = _workspace_path(value)
            command.extend(["--add-dir", str(writable_path)])
        command.extend(
            [
                "--skip-git-repo-check",
                "--cd",
                str(workspace),
                "--output-last-message",
                str(output_path),
                "-",
            ]
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env=_codex_subprocess_env(),
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("Codex CLI executable was not found on PATH.") from exc
        except OSError as exc:
            raise RuntimeError(f"Codex CLI could not be started: {exc}") from exc

        stderr_task = asyncio.create_task(_read_bounded_stream(process.stderr))
        try:
            try:
                await asyncio.wait_for(_feed_prompt_and_wait(process, prompt), timeout=timeout_seconds)
            except TimeoutError as exc:
                raise TimeoutError(f"Codex CLI task timed out after {timeout_seconds} seconds.") from exc
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise RuntimeError(f"Codex CLI communication failed: {exc}") from exc
        finally:
            # Always terminate descendants, including communication failures.
            cleanup_task = asyncio.create_task(_finish_process_cleanup(process, stderr_task))
            stderr_tail = await await_task_through_repeated_cancellation(cleanup_task)

        if process.returncode != 0:
            detail = _bounded_error_text(stderr_tail)
            raise RuntimeError(detail or f"Codex CLI exited with code {process.returncode}.")

        try:
            result = await asyncio.to_thread(output_path.read_text, encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise RuntimeError(f"Codex CLI final message could not be read: {exc}") from exc
        if not result.strip():
            raise RuntimeError("Codex CLI completed without a final message.")
        return result
    finally:
        cleanup_task = asyncio.create_task(asyncio.to_thread(output_path.unlink, missing_ok=True))
        await await_task_through_repeated_cancellation(cleanup_task)
