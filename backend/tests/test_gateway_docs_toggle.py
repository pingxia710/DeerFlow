"""Tests for GATEWAY_ENABLE_DOCS configuration toggle.

Verifies that Swagger UI (/docs), ReDoc (/redoc), and the OpenAPI schema
(/openapi.json) are disabled by default and are only exposed when
GATEWAY_ENABLE_DOCS=true is set explicitly.
"""

from __future__ import annotations

import json
import logging
import os
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


def _reset_gateway_config():
    """Reset the cached gateway config so env changes take effect."""
    import app.gateway.config as cfg

    cfg._gateway_config = None


@pytest.fixture(autouse=True)
def _clean_config():
    """Ensure gateway config cache is cleared before and after each test."""
    _reset_gateway_config()
    yield
    _reset_gateway_config()


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


def test_enable_docs_defaults_to_false():
    """When GATEWAY_ENABLE_DOCS is not set, enable_docs should be False."""
    with patch.dict(os.environ, {}, clear=False):
        if "GATEWAY_ENABLE_DOCS" in os.environ:
            del os.environ["GATEWAY_ENABLE_DOCS"]
        _reset_gateway_config()
        from app.gateway.config import get_gateway_config

        config = get_gateway_config()
        assert config.enable_docs is False


def test_enable_docs_false():
    """GATEWAY_ENABLE_DOCS=false should disable docs."""
    with patch.dict(os.environ, {"GATEWAY_ENABLE_DOCS": "false"}):
        _reset_gateway_config()
        from app.gateway.config import get_gateway_config

        config = get_gateway_config()
        assert config.enable_docs is False


def test_enable_docs_case_insensitive():
    """GATEWAY_ENABLE_DOCS is case-insensitive (FALSE, False, false)."""
    for value in ("FALSE", "False", "false"):
        with patch.dict(os.environ, {"GATEWAY_ENABLE_DOCS": value}):
            _reset_gateway_config()
            from app.gateway.config import get_gateway_config

            config = get_gateway_config()
            assert config.enable_docs is False, f"Expected False for GATEWAY_ENABLE_DOCS={value}"


def test_enable_docs_unexpected_value_disables():
    """Any non-'true' value should disable docs (fail-closed)."""
    for value in ("0", "no", "off", "anything"):
        with patch.dict(os.environ, {"GATEWAY_ENABLE_DOCS": value}):
            _reset_gateway_config()
            from app.gateway.config import get_gateway_config

            config = get_gateway_config()
            assert config.enable_docs is False, f"Expected False for GATEWAY_ENABLE_DOCS={value}"


# ---------------------------------------------------------------------------
# App-level endpoint visibility
# ---------------------------------------------------------------------------


def test_docs_endpoints_disabled_by_default():
    """Without GATEWAY_ENABLE_DOCS, /docs, /redoc, /openapi.json return 404."""
    with patch.dict(os.environ, {}, clear=False):
        if "GATEWAY_ENABLE_DOCS" in os.environ:
            del os.environ["GATEWAY_ENABLE_DOCS"]
        _reset_gateway_config()
        from app.gateway.app import create_app

        app = create_app()
        client = TestClient(app)
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_docs_endpoints_available_when_enabled():
    """With GATEWAY_ENABLE_DOCS=true, /docs, /redoc, /openapi.json return 200."""
    with patch.dict(os.environ, {"GATEWAY_ENABLE_DOCS": "true"}):
        _reset_gateway_config()
        from app.gateway.app import create_app

        app = create_app()
        client = TestClient(app)
        assert client.get("/docs").status_code == 200
        assert client.get("/redoc").status_code == 200
        assert client.get("/openapi.json").status_code == 200


def test_docs_endpoints_disabled_when_false():
    """With GATEWAY_ENABLE_DOCS=false, /docs, /redoc, /openapi.json return 404."""
    with patch.dict(os.environ, {"GATEWAY_ENABLE_DOCS": "false"}):
        _reset_gateway_config()
        from app.gateway.app import create_app

        app = create_app()
        client = TestClient(app)
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_health_still_works_when_docs_disabled():
    """Disabling docs should NOT affect /health or other normal endpoints."""
    with patch.dict(os.environ, {"GATEWAY_ENABLE_DOCS": "false"}):
        _reset_gateway_config()
        from app.gateway.app import create_app

        app = create_app()
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"


def test_gateway_request_id_header_is_returned():
    with patch.dict(os.environ, {"GATEWAY_ENABLE_DOCS": "false"}):
        _reset_gateway_config()
        from app.gateway.app import create_app

        response = TestClient(create_app()).get("/health", headers={"X-Request-ID": "req-123"})

        assert response.headers["x-request-id"] == "req-123"


def test_gateway_request_id_header_is_generated():
    with patch.dict(os.environ, {"GATEWAY_ENABLE_DOCS": "false"}):
        _reset_gateway_config()
        from app.gateway.app import create_app

        response = TestClient(create_app()).get("/health")

        assert response.headers["x-request-id"]


def test_gateway_request_id_is_logged(caplog):
    with patch.dict(os.environ, {"GATEWAY_ENABLE_DOCS": "false"}):
        _reset_gateway_config()
        from app.gateway.app import create_app

        with caplog.at_level(logging.INFO, logger="app.gateway.app"):
            response = TestClient(create_app()).get("/health", headers={"X-Request-ID": "req-log"})

        assert response.status_code == 200
        payloads = [json.loads(record.getMessage()) for record in caplog.records if record.getMessage().startswith('{"event":"gateway.request"')]
        assert payloads[-1]["request_id"] == "req-log"
        assert payloads[-1]["method"] == "GET"
        assert payloads[-1]["path"] == "/health"
        assert payloads[-1]["status_code"] == 200
        assert isinstance(payloads[-1]["duration_ms"], (float, int))


def test_gateway_metrics_endpoint_records_requests():
    with patch.dict(os.environ, {"GATEWAY_ENABLE_DOCS": "false", "DEER_FLOW_AUTH_DISABLED": "1", "ENVIRONMENT": "test"}):
        _reset_gateway_config()
        from app.gateway.app import create_app

        client = TestClient(create_app())
        assert client.get("/health").status_code == 200

        response = client.get("/metrics")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        body = response.text
        assert "deerflow_gateway_uptime_seconds" in body
        assert 'deerflow_gateway_http_requests_total{method="GET",route="/health",status_class="2xx"}' in body
        assert "deerflow_gateway_http_request_duration_seconds_sum" in body


def test_gateway_metrics_use_one_bounded_label_for_unmatched_routes():
    with patch.dict(os.environ, {"GATEWAY_ENABLE_DOCS": "false", "DEER_FLOW_AUTH_DISABLED": "1", "ENVIRONMENT": "test"}):
        _reset_gateway_config()
        from app.gateway.app import _REQUEST_COUNTS, _REQUEST_DURATION_SECONDS, create_app

        _REQUEST_COUNTS.clear()
        _REQUEST_DURATION_SECONDS.clear()
        client = TestClient(create_app())

        assert client.get("/missing-route-one").status_code == 404
        assert client.get("/missing-route-two").status_code == 404
        body = client.get("/metrics").text

        assert 'route="<unmatched>",status_class="4xx"} 2' in body
        assert "/missing-route-one" not in body
        assert "/missing-route-two" not in body


def test_gateway_sanitizes_server_errors_but_preserves_client_errors():
    with patch.dict(os.environ, {"DEER_FLOW_AUTH_DISABLED": "1", "ENVIRONMENT": "test"}):
        _reset_gateway_config()
        from app.gateway.app import create_app

        app = create_app()

        @app.get("/_test/server-error")
        async def server_error():
            raise HTTPException(status_code=500, detail="failed at /Users/private/secret.txt")

        @app.get("/_test/client-error")
        async def client_error():
            raise HTTPException(status_code=409, detail="conflict")

        client = TestClient(app)
        assert client.get("/_test/server-error").json() == {"detail": "Internal server error"}
        assert client.get("/_test/client-error").json() == {"detail": "conflict"}


# ---------------------------------------------------------------------------
# Runtime CORS behavior
# ---------------------------------------------------------------------------


def _make_gateway_client(cors_origins: str) -> TestClient:
    with patch.dict(os.environ, {"GATEWAY_CORS_ORIGINS": cors_origins}):
        _reset_gateway_config()
        from app.gateway.app import create_app

        return TestClient(create_app())


def test_gateway_cors_allows_configured_origin():
    """GATEWAY_CORS_ORIGINS should control actual browser CORS responses."""
    client = _make_gateway_client("https://app.example")

    response = client.get("/health", headers={"Origin": "https://app.example"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://app.example"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_gateway_cors_rejects_unconfigured_origin():
    client = _make_gateway_client("https://app.example")

    response = client.get("/health", headers={"Origin": "https://evil.example"})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_gateway_cors_normalizes_configured_default_port():
    client = _make_gateway_client("https://app.example:443")

    response = client.get("/health", headers={"Origin": "https://app.example"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://app.example"
