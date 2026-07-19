"""Hermetic controller for the independent WP-1 E3-WF and E3-R checks.

It owns only resources recorded in its manifest.  A non-zero result is evidence
of a blocked/failed local run, never a substitute for an E3 pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_BACKEND = Path(__file__).resolve().parents[1]
_REPO = _BACKEND.parent
_FRONTEND = _REPO / "frontend"
_HEALTH_TIMEOUT = 60
_WF_TIMEOUT = 60
_R_GATE_TIMEOUT = 120
_BROWSER_TIMEOUT = 360


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _json(url: str, *, method: str = "GET", body: dict[str, Any] | None = None, timeout: float = 10) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 -- loopback URL created by this process
            raw = response.read().decode()
            return response.status, json.loads(raw) if raw else {}
    except HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"body": raw[:200]}


def _wait(url: str, *, timeout: int) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2) as response:  # noqa: S310 -- loopback URL created by this process
                if response.status == 200:
                    return True
        except (URLError, TimeoutError, OSError):
            pass
        time.sleep(0.25)
    return False


def _wait_file(path: Path, *, timeout: int) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return True
        time.sleep(0.25)
    return False


def _config(root: Path, *, wake_facts: bool) -> str:
    return f"""\
log_level: warning
models:
  - name: e3-chair
    display_name: E3 Chair
    use: e3_control_runtime:E3ChairModel
    model: e3-chair
    supports_thinking: true
sandbox:
  use: deerflow.sandbox.local:LocalSandboxProvider
  allow_host_bash: false
  unrestricted_host_access: false
skills:
  path: {root / "home" / "skills"}
  container_path: /mnt/skills
memory:
  enabled: false
  injection_enabled: false
summarization:
  enabled: false
agents_api:
  enabled: true
database:
  backend: sqlite
  sqlite_dir: {root / "db"}
run_events:
  backend: db
enterprise:
  wake_facts_projection: {"true" if wake_facts else "false"}
subagents:
  model: gpt-5.6-terra
  reasoning_effort: xhigh
  timeout_seconds: 3600
"""


@dataclass
class ProcessRecord:
    role: str
    process: subprocess.Popen[bytes]
    port: int
    pgid: int
    started_at: str


@dataclass
class Scenario:
    name: str
    root: Path
    evidence: Path
    records: list[ProcessRecord] = field(default_factory=list)
    oracles: dict[str, bool] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    frontend_dist: Path | None = None

    def event(self, name: str, **facts: Any) -> None:
        self.events.append({"at": time.monotonic(), "event": name, **facts})
        _write(self.evidence / "timeline.json", self.events)

    def record(self, role: str, process: subprocess.Popen[bytes], port: int) -> ProcessRecord:
        item = ProcessRecord(role, process, port, os.getpgid(process.pid), _process_started(process.pid))
        self.records.append(item)
        _write(
            self.root / "control" / "manifest.json",
            [{"role": r.role, "pid": r.process.pid, "pgid": r.pgid, "port": r.port, "started_at": r.started_at} for r in self.records],
        )
        return item


def _environment(scenario: Scenario, *, mode: str, port: int, nonce: str) -> dict[str, str]:
    for directory in ("home/skills/public", "home/skills/custom", "db", "workspace", "uploads", "outputs", "extensions", "control", "evidence"):
        (scenario.root / directory).mkdir(parents=True, exist_ok=True)
    config = scenario.root / "config.yaml"
    config.write_text(_config(scenario.root, wake_facts=mode == "wf"), encoding="utf-8")
    extensions = scenario.root / "extensions" / "extensions.json"
    extensions.write_text('{"mcpServers":{},"skills":{}}', encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key in {"HOME", "LANG", "LC_ALL", "PATH", "TMPDIR", "VIRTUAL_ENV"}}
    env.update(
        {
            "DEER_FLOW_ENV": "test",
            "DEER_FLOW_HOME": str(scenario.root / "home"),
            "DEER_FLOW_CONFIG_PATH": str(config),
            "DEER_FLOW_EXTENSIONS_CONFIG_PATH": str(extensions),
            "DEERFLOW_E3_TEST": "1",
            "DEERFLOW_E3_ROOT": str(scenario.root),
            "DEERFLOW_E3_MODE": mode,
            "DEERFLOW_E3_BIND_HOST": "127.0.0.1",
            "DEERFLOW_E3_NONCE": nonce,
            "PYTHONPATH": os.pathsep.join((str(_BACKEND), str(_BACKEND / "tests"))),
            "GATEWAY_CORS_ORIGINS": f"http://127.0.0.1:{port}",
            "AUTH_JWT_SECRET": "e3-test-only-secret",
        }
    )
    if mode.startswith("r-"):
        env["DEER_FLOW_AUTH_DISABLED"] = "1"
    return env


def _gateway(scenario: Scenario, *, mode: str, nonce: str) -> tuple[ProcessRecord, dict[str, str]]:
    port = _free_port()
    env = _environment(scenario, mode=mode, port=port, nonce=nonce)
    stdout = (scenario.evidence / f"gateway-{mode}.stdout.log").open("wb")
    stderr = (scenario.evidence / f"gateway-{mode}.stderr.log").open("wb")
    process = subprocess.Popen(
        ["uv", "run", "python", "scripts/run_e3_gateway.py", "--host", "127.0.0.1", "--port", str(port)],
        cwd=_BACKEND,
        env=env,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
    )
    record = scenario.record(f"gateway-{mode}", process, port)
    scenario.event("gateway_started", role=record.role, port=port)
    return record, env


def _stop(record: ProcessRecord, *, hard: bool = False) -> bool:
    if record.process.poll() is not None:
        return True
    try:
        if os.getpgid(record.process.pid) != record.pgid or _process_started(record.process.pid) != record.started_at:
            return False
        os.killpg(record.pgid, signal.SIGKILL if hard else signal.SIGTERM)
        record.process.wait(timeout=10 if hard else 5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if not hard:
            return _stop(record, hard=True)
    return record.process.poll() is not None


def _process_started(pid: int) -> str:
    result = subprocess.run(["/bin/ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, check=False)
    return result.stdout.decode().strip()


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _browser(scenario: Scenario, *, env: dict[str, str], name: str) -> bool:
    browser_env = dict(env)
    browser_env.update({"E3_SCENARIO": name, "E3_EVIDENCE_DIR": str(scenario.evidence)})
    result = subprocess.run(
        ["pnpm", "exec", "playwright", "test", "--config", "playwright.e3.config.ts", "tests/e2e-e3/wp1-wake-facts.spec.ts"],
        cwd=_FRONTEND,
        env=browser_env,
        stdout=(scenario.evidence / "browser.stdout.log").open("wb"),
        stderr=(scenario.evidence / "browser.stderr.log").open("wb"),
        timeout=_BROWSER_TIMEOUT,
        check=False,
    )
    return result.returncode == 0


def _restore_tsconfig(path: Path, original: str, dist_dir: str) -> bool:
    """Restore only Next's own generated include entries, never a user edit."""
    try:
        current = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if current == original:
        return True
    if f'"{dist_dir}/types/' not in current and f'"{dist_dir}/dev/' not in current:
        return False
    path.write_text(original, encoding="utf-8")
    return True


def _frontend(scenario: Scenario, *, gateway_port: int, env: dict[str, str]) -> ProcessRecord | None:
    port = _free_port()
    dist_dir = f".next-e3-{scenario.root.name}"
    scenario.frontend_dist = _FRONTEND / dist_dir
    tsconfig = _FRONTEND / "tsconfig.json"
    tsconfig_before = tsconfig.read_text(encoding="utf-8")
    frontend_env = dict(env)
    frontend_env.update(
        {
            "SKIP_ENV_VALIDATION": "1",
            "BETTER_AUTH_SECRET": "e3-test-only-secret",
            "NEXT_DIST_DIR": dist_dir,
            "PORT": str(port),
            "DEER_FLOW_INTERNAL_GATEWAY_BASE_URL": f"http://127.0.0.1:{gateway_port}",
            "E3_APP_URL": f"http://127.0.0.1:{port}",
            "E3_GATEWAY_URL": f"http://127.0.0.1:{gateway_port}",
        }
    )
    try:
        build = subprocess.run(
            ["pnpm", "build"], cwd=_FRONTEND, env=frontend_env, stdout=(scenario.evidence / "next-build.stdout.log").open("wb"), stderr=(scenario.evidence / "next-build.stderr.log").open("wb"), timeout=_BROWSER_TIMEOUT, check=False
        )
    finally:
        scenario.oracles["tsconfig_restored"] = _restore_tsconfig(tsconfig, tsconfig_before, dist_dir)
    if build.returncode:
        scenario.event("next_build_failed", returncode=build.returncode)
        return None
    process = subprocess.Popen(["pnpm", "start"], cwd=_FRONTEND, env=frontend_env, stdout=(scenario.evidence / "next.stdout.log").open("wb"), stderr=(scenario.evidence / "next.stderr.log").open("wb"), start_new_session=True)
    record = scenario.record("next", process, port)
    if not _wait(f"http://127.0.0.1:{port}", timeout=_BROWSER_TIMEOUT):
        scenario.event("next_not_ready")
        return None
    env.update({"E3_APP_URL": f"http://127.0.0.1:{port}", "E3_GATEWAY_URL": f"http://127.0.0.1:{gateway_port}"})
    return record


def _cleanup(scenario: Scenario, *, passed: bool) -> bool:
    stopped = all(_stop(record) for record in reversed(scenario.records))
    ports_released = all(not _port_open(record.port) for record in scenario.records)
    frontend_dist_removed = True
    if scenario.frontend_dist is not None:
        shutil.rmtree(scenario.frontend_dist, ignore_errors=True)
        frontend_dist_removed = not scenario.frontend_dist.exists()
    cleanup = {
        "registered_processes_exited": stopped,
        "registered_ports_released": ports_released,
        "frontend_dist_removed": frontend_dist_removed,
        "kept_failure_root": not passed,
        "temporary_data_removed": False,
    }
    if passed and stopped and ports_released:
        for name in ("home", "db", "workspace", "uploads", "outputs", "extensions", "next", "config.yaml"):
            target = scenario.root / name
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        cleanup["temporary_data_removed"] = True
    _write(scenario.evidence / "cleanup.json", cleanup)
    inventory = []
    for path in sorted(scenario.evidence.rglob("*")):
        if path.is_file() and path.name != "inventory.sha256":
            inventory.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(scenario.evidence)}")
    (scenario.evidence / "inventory.sha256").write_text("\n".join(inventory) + "\n", encoding="utf-8")
    return stopped and ports_released and frontend_dist_removed


def _run_wf(scenario: Scenario) -> bool:
    nonce = uuid.uuid4().hex
    gateway, env = _gateway(scenario, mode="wf", nonce=nonce)
    base = f"http://127.0.0.1:{gateway.port}"
    scenario.oracles["WF-0"] = _wait(f"{base}/health", timeout=_HEALTH_TIMEOUT)
    if not scenario.oracles["WF-0"]:
        _write(scenario.evidence / "oracles.json", {"E3_WF": False, **scenario.oracles})
        return False
    frontend = _frontend(scenario, gateway_port=gateway.port, env=env)
    scenario.oracles["WF-browser-ready"] = frontend is not None
    if frontend is not None:
        scenario.oracles["WF-1-WF-3"] = _browser(scenario, env=env, name="wf")
    _write(scenario.evidence / "oracles.json", {"E3_WF": all(scenario.oracles.values()), **scenario.oracles})
    return all(scenario.oracles.values())


def _run_r(scenario: Scenario) -> bool:
    nonce = uuid.uuid4().hex
    first, env = _gateway(scenario, mode="r-a", nonce=nonce)
    base_a = f"http://127.0.0.1:{first.port}"
    scenario.oracles["R0"] = _wait(f"{base_a}/health", timeout=_HEALTH_TIMEOUT)
    if not scenario.oracles["R0"]:
        _write(scenario.evidence / "oracles.json", {"E3_R": False, **scenario.oracles})
        return False
    thread_id = f"e3-r-{nonce}"
    created, _ = _json(f"{base_a}/api/threads", method="POST", body={"thread_id": thread_id, "assistant_id": "command-room"})
    started, source = _json(
        f"{base_a}/api/threads/{thread_id}/runs",
        method="POST",
        body={
            "assistant_id": "command-room",
            "input": {"messages": [{"role": "user", "content": f"E3_CHILD_{nonce}"}]},
            "context": {"model_name": "e3-chair"},
            "on_disconnect": "continue",
        },
    )
    scenario.oracles["R1"] = created == 200 and started == 200 and isinstance(source, dict)
    gate = scenario.root / "control" / "outcome-durable.json"
    scenario.oracles["R2"] = scenario.oracles["R1"] and _wait_file(gate, timeout=_R_GATE_TIMEOUT)
    if scenario.oracles["R2"]:
        scenario.oracles["R3"] = _stop(first, hard=True)
        second, env = _gateway(scenario, mode="r-b", nonce=nonce)
        base_b = f"http://127.0.0.1:{second.port}"
        scenario.oracles["R4"] = _wait(f"{base_b}/health", timeout=_HEALTH_TIMEOUT)
        status, observer = _json(f"{base_b}/api/test-only/e3/r/status")
        scenario.oracles["R5"] = status == 200 and observer.get("child_started") == 1
        frontend = _frontend(scenario, gateway_port=second.port, env=env)
        if frontend is not None:
            env["E3_R_THREAD_ID"] = thread_id
            scenario.oracles["R6"] = _browser(scenario, env=env, name="r")
        else:
            scenario.oracles["R6"] = False
    _write(scenario.evidence / "oracles.json", {"E3_R": all(scenario.oracles.values()), **scenario.oracles})
    return all(scenario.oracles.values())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("wf", "r", "all"), default="wf")
    parser.add_argument("--evidence-dir", type=Path, default=Path(tempfile.gettempdir()) / "deerflow-e3-evidence")
    args = parser.parse_args()
    run_root = args.evidence_dir / f"wp1-{uuid.uuid4().hex}"
    run_root.mkdir(parents=True, mode=0o700)
    results: dict[str, bool] = {}
    for name, action in (("wf", _run_wf), ("r", _run_r)):
        if args.scenario not in {name, "all"}:
            continue
        scenario = Scenario(name=name, root=run_root / name, evidence=run_root / name / "evidence")
        scenario.root.mkdir(parents=True, mode=0o700)
        try:
            passed = action(scenario)
        except (OSError, subprocess.SubprocessError, TimeoutError, URLError) as exc:
            scenario.event("blocked_or_failed", kind=type(exc).__name__)
            passed = False
        except BaseException as exc:
            scenario.event("interrupted", kind=type(exc).__name__)
            passed = False
        finally:
            scenario.oracles["cleanup"] = _cleanup(scenario, passed=passed)
        results[f"E3_{name.upper()}"] = passed and scenario.oracles["cleanup"]
        _write(scenario.evidence / "result.json", {"aggregate": results[f"E3_{name.upper()}"], "oracles": scenario.oracles})
    _write(run_root / "aggregate.json", results)
    return 0 if results and all(results.values()) else 1


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main())
