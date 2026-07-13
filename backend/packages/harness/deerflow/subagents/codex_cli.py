"""Run one DeerFlow task through the installed Codex CLI."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import tempfile
from dataclasses import dataclass
from pathlib import Path

_TERMINATE_GRACE_SECONDS = 10
_MAX_ERROR_CHARS = 4_000


class CodexCliError(RuntimeError):
    """The Codex CLI could not complete a delegated task."""


class CodexCliTimeoutError(CodexCliError):
    """The delegated Codex CLI process exceeded its task timeout."""


@dataclass(frozen=True)
class CodexCliTaskResult:
    """Final output produced by a one-shot Codex CLI task."""

    result: str


def _resolve_workspace_path(workspace_path: str | Path | None) -> Path:
    if not workspace_path:
        raise CodexCliError("No thread workspace is available for the Codex CLI task.")

    workspace = Path(workspace_path).expanduser().resolve()
    if not workspace.is_dir():
        raise CodexCliError(f"Codex CLI workspace does not exist: {workspace}")
    return workspace


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return

    try:
        if process.pid:
            os.killpg(process.pid, signal.SIGINT)
        else:
            process.send_signal(signal.SIGINT)
    except (AttributeError, OSError, ProcessLookupError):
        try:
            process.send_signal(signal.SIGINT)
        except (AttributeError, OSError, ProcessLookupError):
            return

    try:
        await asyncio.wait_for(process.wait(), timeout=_TERMINATE_GRACE_SECONDS)
        return
    except TimeoutError:
        pass

    try:
        if process.pid:
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except (AttributeError, OSError, ProcessLookupError):
        try:
            process.kill()
        except (AttributeError, OSError, ProcessLookupError):
            return
    await process.wait()


def _last_agent_message(stdout: str) -> str:
    """Best-effort fallback when the CLI did not write its final-message file."""
    for line in reversed(stdout.splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item") if isinstance(event, dict) else None
        if not isinstance(item, dict) or item.get("type") not in {"agent_message", "message"}:
            continue
        text = item.get("text") or item.get("content")
        if isinstance(text, str) and text.strip():
            return text
    return ""


async def run_codex_cli_task(
    prompt: str,
    *,
    workspace_path: str | Path | None,
    timeout_seconds: int,
    model: str | None = None,
    reasoning_effort: str | None = None,
    binary: str = "codex",
) -> CodexCliTaskResult:
    """Let Codex CLI own the model/tool loop for one natural-language task."""
    workspace = _resolve_workspace_path(workspace_path)
    output_file = tempfile.NamedTemporaryFile(prefix="deerflow-codex-", suffix=".txt", delete=False)
    output_path = Path(output_file.name)
    output_file.close()

    try:
        try:
            command = [binary, "exec"]
            if model:
                command.extend(["--model", model])
            if reasoning_effort:
                command.extend(["--config", f"model_reasoning_effort={json.dumps(reasoning_effort)}"])
            command.extend(
                [
                    "--json",
                    "--ephemeral",
                    "--sandbox",
                    "workspace-write",
                    "--skip-git-repo-check",
                    "--cd",
                    str(workspace),
                    "--output-last-message",
                    str(output_path),
                    prompt,
                ]
            )
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise CodexCliError("Codex CLI executable was not found on PATH.") from exc
        except OSError as exc:
            raise CodexCliError(f"Codex CLI could not be started: {exc}") from exc

        stdout_task = asyncio.create_task(process.stdout.read())
        stderr_task = asyncio.create_task(process.stderr.read())
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
        except TimeoutError as exc:
            await _stop_process(process)
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise CodexCliTimeoutError(f"Codex CLI task timed out after {timeout_seconds} seconds.") from exc
        except asyncio.CancelledError:
            await _stop_process(process)
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise

        stdout_bytes, stderr_bytes = await asyncio.gather(stdout_task, stderr_task)
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        if process.returncode != 0:
            detail = (stderr or stdout).strip()[:_MAX_ERROR_CHARS]
            raise CodexCliError(detail or f"Codex CLI exited with code {process.returncode}.")

        result = output_path.read_text(encoding="utf-8").strip() if output_path.exists() else ""
        result = result or _last_agent_message(stdout)
        if not result:
            raise CodexCliError("Codex CLI completed without a final message.")
        return CodexCliTaskResult(result=result)
    finally:
        output_path.unlink(missing_ok=True)
