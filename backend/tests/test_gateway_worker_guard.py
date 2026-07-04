from __future__ import annotations

import pytest

from app.gateway.app import _assert_single_gateway_worker

_ENV_KEYS = ("DEER_FLOW_ENV", "ENVIRONMENT", "APP_ENV", "NODE_ENV")
_WORKER_KEYS = ("GATEWAY_WORKERS", "WEB_CONCURRENCY", "UVICORN_WORKERS")


def _clear_worker_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (*_ENV_KEYS, *_WORKER_KEYS):
        monkeypatch.delenv(key, raising=False)


def test_gateway_worker_guard_defaults_to_strict_when_env_unset(monkeypatch):
    _clear_worker_env(monkeypatch)
    monkeypatch.setenv("GATEWAY_WORKERS", "2")

    with pytest.raises(RuntimeError, match="process-local"):
        _assert_single_gateway_worker()


def test_gateway_worker_guard_allows_one_worker_without_env(monkeypatch):
    _clear_worker_env(monkeypatch)
    monkeypatch.setenv("GATEWAY_WORKERS", "1")

    _assert_single_gateway_worker()


def test_gateway_worker_guard_allows_explicit_dev_env(monkeypatch):
    _clear_worker_env(monkeypatch)
    monkeypatch.setenv("NODE_ENV", "development")
    monkeypatch.setenv("GATEWAY_WORKERS", "4")

    _assert_single_gateway_worker()


def test_gateway_worker_guard_shared_env_overrides_dev_label(monkeypatch):
    _clear_worker_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DEER_FLOW_ENV", "staging")
    monkeypatch.setenv("GATEWAY_WORKERS", "2")

    with pytest.raises(RuntimeError, match="process-local"):
        _assert_single_gateway_worker()
