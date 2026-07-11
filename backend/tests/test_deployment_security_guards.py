from importlib import import_module
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers

import app.gateway.routers.auth as auth_router
from app.gateway.app import (
    _assert_run_event_store_config_for_environment,
    _assert_safe_sandbox_config_for_environment,
)
from app.gateway.routers.auth import InitializeAdminRequest, _validate_first_boot_setup_access
from app.gateway.routers.runs import stateless_wait
from app.gateway.routers.thread_runs import run_error_for_response
from deerflow.config.app_config import AppConfig
from deerflow.config.database_config import DatabaseConfig
from deerflow.config.run_events_config import RunEventsConfig
from deerflow.config.sandbox_config import SandboxConfig

_ENV_KEYS = ("DEER_FLOW_ENV", "ENVIRONMENT", "APP_ENV", "NODE_ENV")
REPO_ROOT = Path(__file__).resolve().parents[2]
gateway_app = import_module("app.gateway.app")


class DummyClient:
    def __init__(self, host: str):
        self.host = host


class DummyRequest:
    def __init__(self, host: str, headers: dict[str, str] | None = None):
        self.client = DummyClient(host)
        self.headers = Headers(headers or {})


def _init_body(token: str | None = None) -> InitializeAdminRequest:
    return InitializeAdminRequest(email="admin@example.com", password="StrongPassword123", setup_token=token)


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _config(
    *,
    database_backend: str = "memory",
    run_events_backend: str = "memory",
    **sandbox_overrides,
) -> AppConfig:
    return AppConfig(
        database=DatabaseConfig(backend=database_backend),
        run_events=RunEventsConfig(backend=run_events_backend),
        sandbox=SandboxConfig(
            use="deerflow.sandbox.local:LocalSandboxProvider",
            **sandbox_overrides,
        ),
    )


def test_initialize_allows_loopback_without_setup_token(monkeypatch):
    monkeypatch.delenv("DEER_FLOW_SETUP_TOKEN", raising=False)
    _validate_first_boot_setup_access(DummyRequest("127.0.0.1"), _init_body())


def test_initialize_rejects_remote_without_token_or_explicit_allow(monkeypatch):
    monkeypatch.delenv("DEER_FLOW_SETUP_TOKEN", raising=False)
    monkeypatch.delenv("DEER_FLOW_ALLOW_REMOTE_INITIALIZE", raising=False)

    with pytest.raises(HTTPException) as exc:
        _validate_first_boot_setup_access(DummyRequest("203.0.113.10"), _init_body())

    assert exc.value.status_code == 403
    assert "setup token" in exc.value.detail


def test_initialize_allows_localhost_through_loopback_bound_docker_proxy(monkeypatch):
    monkeypatch.delenv("DEER_FLOW_SETUP_TOKEN", raising=False)
    monkeypatch.delenv("DEER_FLOW_ALLOW_REMOTE_INITIALIZE", raising=False)
    monkeypatch.setenv("DEER_FLOW_PUBLIC_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("AUTH_TRUSTED_PROXIES", "172.20.0.0/16")

    _validate_first_boot_setup_access(
        DummyRequest("172.20.0.3", {"host": "localhost:2026"}),
        _init_body(),
    )


def test_initialize_rejects_proxy_shortcut_when_public_bind_is_remote(monkeypatch):
    monkeypatch.delenv("DEER_FLOW_SETUP_TOKEN", raising=False)
    monkeypatch.delenv("DEER_FLOW_ALLOW_REMOTE_INITIALIZE", raising=False)
    monkeypatch.setenv("DEER_FLOW_PUBLIC_BIND_HOST", "0.0.0.0")

    with pytest.raises(HTTPException) as exc:
        _validate_first_boot_setup_access(
            DummyRequest("172.20.0.3", {"host": "localhost:2026"}),
            _init_body(),
        )

    assert exc.value.status_code == 403


def test_initialize_rejects_loopback_proxy_peer_on_remote_public_bind(monkeypatch):
    monkeypatch.delenv("DEER_FLOW_SETUP_TOKEN", raising=False)
    monkeypatch.delenv("DEER_FLOW_ALLOW_REMOTE_INITIALIZE", raising=False)
    monkeypatch.setenv("DEER_FLOW_PUBLIC_BIND_HOST", "0.0.0.0")

    with pytest.raises(HTTPException) as exc:
        _validate_first_boot_setup_access(
            DummyRequest(
                "127.0.0.1",
                {
                    "host": "192.168.1.10:2026",
                    "x-forwarded-for": "192.168.1.50",
                },
            ),
            _init_body(),
        )

    assert exc.value.status_code == 403


def test_initialize_rejects_untrusted_private_proxy(monkeypatch):
    monkeypatch.delenv("DEER_FLOW_SETUP_TOKEN", raising=False)
    monkeypatch.delenv("DEER_FLOW_ALLOW_REMOTE_INITIALIZE", raising=False)
    monkeypatch.setenv("DEER_FLOW_PUBLIC_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("AUTH_TRUSTED_PROXIES", "10.0.0.0/8")

    with pytest.raises(HTTPException) as exc:
        _validate_first_boot_setup_access(
            DummyRequest("172.20.0.3", {"host": "localhost:2026"}),
            _init_body(),
        )

    assert exc.value.status_code == 403


def test_initialize_accepts_setup_token_header(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_SETUP_TOKEN", "expected-token")
    _validate_first_boot_setup_access(
        DummyRequest("203.0.113.10", {"x-deer-flow-setup-token": "expected-token"}),
        _init_body(),
    )


def test_initialize_rejects_bad_setup_token(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_SETUP_TOKEN", "expected-token")

    with pytest.raises(HTTPException) as exc:
        _validate_first_boot_setup_access(DummyRequest("127.0.0.1"), _init_body("wrong"))

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_stateless_wait_sanitizes_failed_run_error(monkeypatch):
    from types import SimpleNamespace

    from deerflow.runtime import RunStatus

    async def fake_start_run(body, thread_id, request):
        return SimpleNamespace(run_id="run-1", task=None, status=RunStatus.error, error="secret raw stack trace")

    monkeypatch.setattr("app.gateway.routers.runs.start_run", fake_start_run)
    monkeypatch.setattr("app.gateway.routers.runs.get_stream_bridge", lambda request: object())

    class FakeRunManager:
        async def get(self, run_id, *, user_id=None):
            return None

    monkeypatch.setattr("app.gateway.routers.runs.get_run_manager", lambda request: FakeRunManager())

    result = await stateless_wait.__wrapped__(SimpleNamespace(config={}), object())

    assert result == {"status": "error", "error": "Run failed"}


def test_run_error_response_extracts_traceback_message():
    error = """Traceback (most recent call last):
  File "/private/app.py", line 1, in <module>
RuntimeError: boom"""

    assert run_error_for_response(error) == "boom"


def test_run_error_response_hides_traceback_without_public_message():
    error = """Traceback (most recent call last):
  File "/private/app.py", line 1, in <module>
    raise RuntimeError("hidden")"""

    assert run_error_for_response(error) == "Run failed"


@pytest.mark.parametrize(
    "error",
    [
        "FileNotFoundError: [Errno 2] No such file or directory: '/srv/deerflow/private/config.yaml'",
        "RuntimeError: internal runtime failure",
        "Unable to read /srv/deerflow/private/config.yaml",
    ],
)
def test_run_error_response_hides_single_line_exception_internals(error: str) -> None:
    assert run_error_for_response(error) == "Run failed"


def test_run_error_response_hides_codex_stream_incomplete_detail():
    error = "LLM request failed: Codex API stream ended without response.completed event"

    public_error = run_error_for_response(error)

    assert public_error is not None
    assert "temporarily unavailable" in public_error
    assert "response.completed" not in public_error


def test_run_error_response_hides_codex_stream_incomplete_traceback_detail():
    error = """Traceback (most recent call last):
  File "/private/app.py", line 1, in <module>
CodexStreamIncompleteError: Codex API stream ended without response.completed event"""

    public_error = run_error_for_response(error)

    assert public_error is not None
    assert "temporarily unavailable" in public_error
    assert "response.completed" not in public_error


def test_dangerous_sandbox_settings_allowed_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("NODE_ENV", "development")

    _assert_safe_sandbox_config_for_environment(
        _config(
            allow_host_bash=True,
            unrestricted_host_access=True,
            allow_dangerous_host_mounts=True,
            seccomp_unconfined=True,
        )
    )


def test_dangerous_sandbox_settings_allowed_when_environment_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)

    _assert_safe_sandbox_config_for_environment(_config(allow_host_bash=True))


@pytest.mark.parametrize(
    "field",
    [
        "allow_host_bash",
        "unrestricted_host_access",
        "allow_dangerous_host_mounts",
        "seccomp_unconfined",
    ],
)
def test_dangerous_sandbox_settings_fail_fast_in_production(monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")

    with pytest.raises(RuntimeError, match=field):
        _assert_safe_sandbox_config_for_environment(_config(**{field: True}))


def test_safe_sandbox_settings_allowed_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")

    _assert_safe_sandbox_config_for_environment(_config())


def test_remote_bind_rejects_trusted_host_access_even_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("NODE_ENV", "development")
    monkeypatch.setenv("DEER_FLOW_BIND_HOST", "0.0.0.0")

    guard = getattr(gateway_app, "_assert_safe_local_exposure_config", None)
    assert callable(guard), "gateway must expose the local host-access bind guard"
    with pytest.raises(RuntimeError, match="unrestricted_host_access"):
        guard(_config(unrestricted_host_access=True))


def test_remote_public_edge_rejects_trusted_host_access(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("NODE_ENV", "development")
    monkeypatch.setenv("DEER_FLOW_PUBLIC_BIND_HOST", "0.0.0.0")

    guard = getattr(gateway_app, "_assert_safe_local_exposure_config", None)
    assert callable(guard), "gateway must expose the local host-access bind guard"
    with pytest.raises(RuntimeError, match="unrestricted_host_access"):
        guard(_config(unrestricted_host_access=True))


def test_loopback_bind_allows_trusted_host_access(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("DEER_FLOW_BIND_HOST", "127.0.0.1")

    guard = getattr(gateway_app, "_assert_safe_local_exposure_config", None)
    assert callable(guard), "gateway must expose the local host-access bind guard"
    guard(_config(unrestricted_host_access=True))


@pytest.mark.parametrize(
    "bind_key",
    [
        "DEER_FLOW_PUBLIC_BIND_HOST",
        "DEER_FLOW_BIND_HOST",
        "DEER_FLOW_GATEWAY_HOST",
        "GATEWAY_HOST",
    ],
)
def test_remote_bind_rejects_auth_disabled(
    monkeypatch: pytest.MonkeyPatch,
    bind_key: str,
) -> None:
    _clear_env(monkeypatch)
    for key in (
        "DEER_FLOW_PUBLIC_BIND_HOST",
        "DEER_FLOW_BIND_HOST",
        "DEER_FLOW_GATEWAY_HOST",
        "GATEWAY_HOST",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv(bind_key, "0.0.0.0")
    monkeypatch.setenv("DEER_FLOW_AUTH_DISABLED", "1")

    with pytest.raises(RuntimeError, match="authentication"):
        gateway_app._assert_safe_local_exposure_config(_config())


def test_docker_default_public_bind_rejects_auth_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    dockerfile = (REPO_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    default_bindings = [line.removeprefix("ENV DEER_FLOW_PUBLIC_BIND_HOST=").strip() for line in dockerfile.splitlines() if line.startswith("ENV DEER_FLOW_PUBLIC_BIND_HOST=")]
    assert default_bindings == ["0.0.0.0", "0.0.0.0"], "standalone dev and runtime images must default to remote public exposure"

    _clear_env(monkeypatch)
    for key in (
        "DEER_FLOW_PUBLIC_BIND_HOST",
        "DEER_FLOW_BIND_HOST",
        "DEER_FLOW_GATEWAY_HOST",
        "GATEWAY_HOST",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DEER_FLOW_PUBLIC_BIND_HOST", default_bindings[-1])
    monkeypatch.setenv("DEER_FLOW_AUTH_DISABLED", "1")

    with pytest.raises(RuntimeError, match="authentication"):
        gateway_app._assert_safe_local_exposure_config(_config())


def test_docker_loopback_public_edge_allows_trusted_local_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    dockerfile = (REPO_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")

    _clear_env(monkeypatch)
    for key in (
        "DEER_FLOW_PUBLIC_BIND_HOST",
        "DEER_FLOW_BIND_HOST",
        "DEER_FLOW_GATEWAY_HOST",
        "GATEWAY_HOST",
    ):
        monkeypatch.delenv(key, raising=False)
        prefix = f"ENV {key}="
        image_defaults = [line.removeprefix(prefix).strip() for line in dockerfile.splitlines() if line.startswith(prefix)]
        if image_defaults:
            monkeypatch.setenv(key, image_defaults[-1])

    monkeypatch.setenv("NODE_ENV", "development")
    monkeypatch.setenv("DEER_FLOW_PUBLIC_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("DEER_FLOW_AUTH_DISABLED", "0")

    gateway_app._assert_safe_local_exposure_config(
        _config(
            allow_host_bash=True,
            unrestricted_host_access=True,
            allow_dangerous_host_mounts=True,
        )
    )


def test_legacy_remote_gateway_bind_cannot_be_masked_by_loopback_public_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("DEER_FLOW_PUBLIC_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("DEER_FLOW_GATEWAY_HOST", "0.0.0.0")

    with pytest.raises(RuntimeError, match="0.0.0.0"):
        gateway_app._assert_safe_local_exposure_config(_config(unrestricted_host_access=True))

    with pytest.raises(HTTPException) as registration_exc:
        auth_router._assert_registration_allowed()
    assert registration_exc.value.status_code == 403

    with pytest.raises(HTTPException) as initialize_exc:
        _validate_first_boot_setup_access(
            DummyRequest("127.0.0.1", {"host": "localhost:8001"}),
            _init_body(),
        )
    assert initialize_exc.value.status_code == 403


def test_registration_defaults_closed_in_shared_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.delenv("DEER_FLOW_ALLOW_REGISTRATION", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")

    guard = getattr(auth_router, "_assert_registration_allowed", None)
    assert callable(guard), "auth router must expose the registration policy guard"
    with pytest.raises(HTTPException) as exc:
        guard()

    assert exc.value.status_code == 403


def test_registration_can_be_explicitly_enabled_in_shared_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DEER_FLOW_ALLOW_REGISTRATION", "true")

    guard = getattr(auth_router, "_assert_registration_allowed", None)
    assert callable(guard), "auth router must expose the registration policy guard"
    guard()


def test_registration_defaults_closed_on_remote_development_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.delenv("DEER_FLOW_ALLOW_REGISTRATION", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DEER_FLOW_PUBLIC_BIND_HOST", "0.0.0.0")

    with pytest.raises(HTTPException) as exc:
        auth_router._assert_registration_allowed()

    assert exc.value.status_code == 403


def test_registration_defaults_open_on_loopback_development_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.delenv("DEER_FLOW_ALLOW_REGISTRATION", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DEER_FLOW_PUBLIC_BIND_HOST", "127.0.0.1")

    auth_router._assert_registration_allowed()


@pytest.mark.parametrize("backend", ["memory", "jsonl"])
def test_run_event_store_non_db_fails_fast_in_production(monkeypatch: pytest.MonkeyPatch, backend: str) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")

    with pytest.raises(RuntimeError, match="run_events.backend='db'"):
        _assert_run_event_store_config_for_environment(_config(run_events_backend=backend))


def test_run_event_store_db_requires_persistent_database_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")

    with pytest.raises(RuntimeError, match="database.backend"):
        _assert_run_event_store_config_for_environment(_config(run_events_backend="db"))


def test_run_event_store_db_allowed_with_sqlite_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")

    _assert_run_event_store_config_for_environment(_config(database_backend="sqlite", run_events_backend="db"))


def test_run_event_store_jsonl_allowed_when_environment_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)

    _assert_run_event_store_config_for_environment(_config(run_events_backend="jsonl"))
