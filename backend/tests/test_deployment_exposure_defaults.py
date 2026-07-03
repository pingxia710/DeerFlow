from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _compose(path: str) -> dict:
    return yaml.safe_load((REPO_ROOT / path).read_text(encoding="utf-8"))


def _command(service: dict) -> str:
    command = service["command"]
    if isinstance(command, list):
        return " ".join(str(part) for part in command)
    return str(command)


def test_docker_prod_nginx_defaults_to_localhost_and_gated_sensitive_routes():
    nginx = _compose("docker/docker-compose.yaml")["services"]["nginx"]

    assert "${DEER_FLOW_BIND_HOST:-127.0.0.1}:${PORT:-2026}:2026" in nginx["ports"]
    assert "DEER_FLOW_EXPOSE_API_DOCS=${DEER_FLOW_EXPOSE_API_DOCS:-false}" in nginx["environment"]
    assert "DEER_FLOW_EXPOSE_SANDBOX_API=${DEER_FLOW_EXPOSE_SANDBOX_API:-false}" in nginx["environment"]
    command = _command(nginx)
    assert "/etc/nginx/conf.d/deer-flow-exposure.conf" in command
    assert "nginx -t" in command


def test_docker_dev_nginx_defaults_to_localhost_and_gated_sensitive_routes():
    nginx = _compose("docker/docker-compose-dev.yaml")["services"]["nginx"]

    assert "${DEER_FLOW_BIND_HOST:-127.0.0.1}:2026:2026" in nginx["ports"]
    assert "DEER_FLOW_EXPOSE_API_DOCS=${DEER_FLOW_EXPOSE_API_DOCS:-false}" in nginx["environment"]
    assert "DEER_FLOW_EXPOSE_SANDBOX_API=${DEER_FLOW_EXPOSE_SANDBOX_API:-false}" in nginx["environment"]
    command = _command(nginx)
    assert "/etc/nginx/conf.d/deer-flow-exposure.conf" in command
    assert "nginx -t" in command


def test_serve_script_uses_safe_dotenv_parser_and_localhost_default():
    script = (REPO_ROOT / "scripts" / "serve.sh").read_text(encoding="utf-8")

    assert 'source "$REPO_ROOT/.env"' not in script
    assert "^[A-Za-z_][A-Za-z0-9_]*$" in script
    assert 'GATEWAY_HOST="${DEER_FLOW_BIND_HOST:-${DEER_FLOW_GATEWAY_HOST:-127.0.0.1}}"' in script


def test_backend_makefile_gateway_targets_default_to_localhost():
    makefile = (REPO_ROOT / "backend" / "Makefile").read_text(encoding="utf-8")

    assert "--host 0.0.0.0" not in makefile
    assert "GATEWAY_BIND_HOST" in makefile
    assert "DEER_FLOW_BIND_HOST" in makefile
    assert "127.0.0.1" in makefile


def test_nginx_sensitive_routes_are_opt_in():
    config = (REPO_ROOT / "docker" / "nginx" / "nginx.conf").read_text(encoding="utf-8")

    assert "include /etc/nginx/conf.d/deer-flow-exposure.conf;" in config
    assert "if ($expose_api_docs = 0) { return 404; }" in config
    assert "if ($expose_sandbox_api = 0) { return 404; }" in config


def test_gateway_host_defaults_to_localhost(monkeypatch):
    import app.gateway.config as cfg
    from app.gateway.config import get_gateway_config

    monkeypatch.delenv("GATEWAY_HOST", raising=False)
    cfg._gateway_config = None
    assert get_gateway_config().host == "127.0.0.1"

    monkeypatch.setenv("GATEWAY_HOST", "0.0.0.0")
    cfg._gateway_config = None
    assert get_gateway_config().host == "0.0.0.0"
