from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "docker" / "nginx" / "generate-config.sh"


def _generate_config(
    tmp_path: Path,
    *,
    trusted_outer_proxies: str | None = None,
    existing: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    output = tmp_path / "deer-flow-exposure.conf"
    if existing is not None:
        output.write_text(existing, encoding="utf-8")

    env = os.environ.copy()
    env.pop("DEER_FLOW_TRUSTED_OUTER_PROXIES", None)
    env["DEER_FLOW_EXPOSE_API_DOCS"] = "false"
    if trusted_outer_proxies is not None:
        env["DEER_FLOW_TRUSTED_OUTER_PROXIES"] = trusted_outer_proxies

    result = subprocess.run(
        ["sh", str(GENERATOR), str(output)],
        capture_output=True,
        check=False,
        env=env,
        text=True,
    )
    return result, output


def test_nginx_does_not_trust_forwarded_for_without_explicit_outer_proxy(tmp_path, monkeypatch):
    result, output = _generate_config(tmp_path)

    assert result.returncode == 0, result.stderr
    generated = output.read_text(encoding="utf-8")
    assert "set $expose_api_docs 0;" in generated
    assert "set_real_ip_from" not in generated
    assert "real_ip_header" not in generated
    assert "real_ip_recursive" not in generated

    # Without a trusted outer proxy, nginx overwrites X-Real-IP with the TCP
    # peer. Rotating a forged X-Forwarded-For value must therefore stay in one
    # Gateway login bucket instead of bypassing the limit.
    monkeypatch.setenv("AUTH_TRUSTED_PROXIES", "172.16.0.0/12")
    from app.gateway.routers.auth import (
        _check_rate_limit,
        _get_client_ip,
        _login_attempts,
        _record_login_failure,
    )

    def request(forged_xff: str) -> SimpleNamespace:
        return SimpleNamespace(
            client=SimpleNamespace(host="172.18.0.3"),
            headers={
                "x-real-ip": "198.51.100.10",
                "x-forwarded-for": forged_xff,
            },
        )

    _login_attempts.clear()
    try:
        for index in range(5):
            _record_login_failure(_get_client_ip(request(f"203.0.113.{index + 1}")))
        with pytest.raises(HTTPException, match="Too many login attempts"):
            _check_rate_limit(_get_client_ip(request("203.0.113.99")))
    finally:
        _login_attempts.clear()


def test_trusted_outer_proxy_keeps_client_login_buckets_independent(tmp_path, monkeypatch):
    result, output = _generate_config(
        tmp_path,
        trusted_outer_proxies="10.0.0.0/8, 2001:db8::/32",
    )

    assert result.returncode == 0, result.stderr
    generated = output.read_text(encoding="utf-8")
    assert "set_real_ip_from 10.0.0.0/8;" in generated
    assert "set_real_ip_from 2001:db8::/32;" in generated
    assert "real_ip_header X-Forwarded-For;" in generated
    assert "real_ip_recursive on;" in generated

    # nginx resolves the trusted X-Forwarded-For chain into X-Real-IP before
    # forwarding to the Gateway. The Gateway must retain one bucket per client,
    # even though both requests have the same nginx TCP peer.
    monkeypatch.setenv("AUTH_TRUSTED_PROXIES", "172.16.0.0/12")
    from app.gateway.routers.auth import (
        _check_rate_limit,
        _get_client_ip,
        _login_attempts,
        _record_login_failure,
    )

    def request(real_ip: str) -> SimpleNamespace:
        return SimpleNamespace(
            client=SimpleNamespace(host="172.18.0.3"),
            headers={"x-real-ip": real_ip},
        )

    first_ip = _get_client_ip(request("198.51.100.1"))
    second_ip = _get_client_ip(request("203.0.113.2"))
    assert first_ip != second_ip

    _login_attempts.clear()
    try:
        for _ in range(5):
            _record_login_failure(first_ip)
        with pytest.raises(HTTPException, match="Too many login attempts"):
            _check_rate_limit(first_ip)
        _check_rate_limit(second_ip)
    finally:
        _login_attempts.clear()


def test_trusted_outer_proxy_config_rejects_directive_injection(tmp_path):
    existing = "known-good\n"
    result, output = _generate_config(
        tmp_path,
        trusted_outer_proxies="127.0.0.1/32; include /tmp/attacker.conf",
        existing=existing,
    )

    assert result.returncode != 0
    assert output.read_text(encoding="utf-8") == existing
    assert "invalid trusted outer proxy CIDR" in result.stderr


@pytest.mark.parametrize("invalid_value", ["10.0.0.1", "dead.beef/24"])
def test_trusted_outer_proxy_config_requires_numeric_cidr(tmp_path, invalid_value):
    result, _ = _generate_config(
        tmp_path,
        trusted_outer_proxies=invalid_value,
    )

    assert result.returncode != 0
    assert "invalid trusted outer proxy CIDR" in result.stderr
