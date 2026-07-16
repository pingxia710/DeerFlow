from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


@pytest.mark.parametrize("compose_path", ["docker/docker-compose.yaml", "docker/docker-compose-dev.yaml"])
def test_compose_env_files_are_optional(compose_path: str):
    compose = yaml.safe_load(_read(compose_path))

    for service_name in ("frontend", "gateway"):
        env_files = compose["services"][service_name]["env_file"]
        assert env_files
        assert all(entry["required"] is False for entry in env_files), service_name


def test_local_nginx_preserves_forwarded_https_scheme():
    config = _read("docker/nginx/nginx.local.conf")

    assert "map $http_x_forwarded_proto $forwarded_proto" in config
    assert "proxy_set_header X-Forwarded-Proto $scheme;" not in config
    assert "proxy_set_header X-Forwarded-Proto $forwarded_proto;" in config


def test_local_launcher_builds_before_start_and_monitors_services():
    script = _read("scripts/serve.sh")

    assert "pnpm run build &&" not in script
    assert 'pnpm run build) || { echo "✗ Frontend production build failed"; exit 1; }' in script
    assert './scripts/wait-for-port.sh "$port" "$timeout" "$name" "$pid"' in script
    assert "SERVICE_PIDS" in script
    assert "_wait_for_any_service_exit" in script
    assert '_wait_for_port_release "$GATEWAY_PORT" "Gateway"' in script
    assert "wait -n" not in script


def test_local_gateway_keeps_ambient_proxy_and_protects_its_log():
    script = _read("scripts/serve.sh")

    assert "NO_PROXY='*' no_proxy='*'" not in script
    assert ": > logs/gateway.log" in script
    assert "chmod 600 logs/gateway.log" in script


def test_wait_for_port_fails_fast_when_service_pid_exits():
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required")

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    process = subprocess.Popen([bash, "-c", "exit 7"])
    process.wait(timeout=2)
    started = time.monotonic()
    result = subprocess.run(
        [bash, str(REPO_ROOT / "scripts/wait-for-port.sh"), str(port), "10", "Probe", str(process.pid)],
        capture_output=True,
        text=True,
        timeout=3,
    )

    assert result.returncode == 1
    assert time.monotonic() - started < 3
    assert "exited before listening" in result.stdout


def test_compose_launchers_wait_for_health_and_dump_logs_on_failure():
    for path in ("scripts/docker.sh", "scripts/deploy.sh"):
        script = _read(path)
        assert "--wait --wait-timeout 240" in script, path
        assert "logs --no-color --tail=200" in script, path

    docker_script = _read("scripts/docker.sh")
    assert "ps --services --status running" in docker_script
    assert "up -d --no-build --no-recreate --wait --wait-timeout 240" in docker_script


def test_docker_dev_logs_flow_to_the_container_logging_driver():
    compose = _read("docker/docker-compose-dev.yaml")
    entrypoint = _read("docker/dev-entrypoint.sh")
    deploy = _read("scripts/deploy.sh")

    assert "/app/logs/frontend.log" not in compose
    assert "/app/logs/gateway.log" not in entrypoint
    assert "./scripts/deploy.sh logs" in deploy
    assert "logs)" in deploy


@pytest.mark.parametrize(
    ("command", "expected_tail"),
    [
        ("logs", ["logs", "-f"]),
        ("down", ["down"]),
        ("build", ["build"]),
    ],
)
def test_production_control_commands_do_not_create_runtime_state(
    tmp_path: Path,
    command: str,
    expected_tail: list[str],
):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_args = tmp_path / "docker-args.txt"
    fake_docker = bin_dir / "docker"
    fake_docker.write_text(
        '#!/bin/sh\nif [ "$1" = "compose" ] && [ "$2" = "version" ]; then\n    printf "%s\\n" "${DEERFLOW_COMPOSE_VERSION:-2.24.0}"\n    exit 0\nfi\nprintf "%s\\n" "$@" > "$DEERFLOW_DOCKER_ARGS_FILE"\n',
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    env = os.environ.copy()
    state_paths = {
        "DEER_FLOW_HOME": tmp_path / "home",
        "DEER_FLOW_CONFIG_PATH": tmp_path / "config.yaml",
        "DEER_FLOW_EXTENSIONS_CONFIG_PATH": tmp_path / "extensions.json",
    }
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
            "DEERFLOW_DOCKER_ARGS_FILE": str(docker_args),
            **{key: str(path) for key, path in state_paths.items()},
        }
    )
    env.pop("BETTER_AUTH_SECRET", None)
    env.pop("DEER_FLOW_INTERNAL_AUTH_TOKEN", None)
    result = subprocess.run(
        [bash, str(REPO_ROOT / "scripts/deploy.sh"), command],
        capture_output=True,
        text=True,
        timeout=5,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert docker_args.read_text(encoding="utf-8").splitlines()[-len(expected_tail) :] == expected_tail
    assert all(not path.exists() for path in state_paths.values())


def test_production_entrypoint_rejects_compose_before_optional_env_support(
    tmp_path: Path,
):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_docker = bin_dir / "docker"
    fake_docker.write_text(
        '#!/bin/sh\nif [ "$1" = "compose" ] && [ "$2" = "version" ]; then\n    printf "%s\\n" "2.23.3"\n    exit 0\nfi\nexit 99\n',
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    result = subprocess.run(
        [bash, str(REPO_ROOT / "scripts/deploy.sh"), "logs"],
        capture_output=True,
        text=True,
        timeout=5,
        env=env,
    )

    assert result.returncode == 1
    assert "2.24.0 or newer is required" in result.stderr


def test_docker_runtime_smoke_tracks_launcher_changes():
    workflow = _read(".github/workflows/docker-smoke.yml")

    for path in (
        "Makefile",
        "scripts/docker.sh",
        "scripts/deploy.sh",
        "scripts/serve.sh",
        "scripts/wait-for-port.sh",
    ):
        assert workflow.count(f'- "{path}"') == 2, path
