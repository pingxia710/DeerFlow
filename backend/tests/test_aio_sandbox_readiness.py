from __future__ import annotations

from types import SimpleNamespace

import pytest

from deerflow.community.aio_sandbox import backend as readiness


def _response(status_code: int) -> SimpleNamespace:
    return SimpleNamespace(status_code=status_code)


def test_wait_for_sandbox_ready_verifies_missing_and_wrong_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    def recording_get(_url: str, *, timeout: float, headers: dict[str, str] | None = None):
        del timeout
        headers_log = headers
        headers_seen.append(headers_log)
        return next(response_iter)

    response_iter = iter([_response(200), _response(401), _response(403)])
    headers_seen: list[dict[str, str] | None] = []
    monkeypatch.setattr(readiness.requests, "get", recording_get)

    assert readiness.wait_for_sandbox_ready("http://sandbox", timeout=5, api_key="expected-token") is True
    assert headers_seen[0] == {"X-AIO-API-Key": "expected-token"}
    assert headers_seen[1] is None
    assert headers_seen[2]
    assert headers_seen[2]["X-AIO-API-Key"] != "expected-token"


def test_wait_for_sandbox_ready_rejects_image_that_ignores_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter([_response(200), _response(200)])
    monkeypatch.setattr(
        readiness.requests,
        "get",
        lambda *_args, **_kwargs: next(responses),
    )

    assert readiness.wait_for_sandbox_ready("http://sandbox", timeout=5, api_key="expected-token") is False


def test_wait_for_sandbox_ready_surfaces_negative_auth_probe_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter(
        [
            _response(200),
            readiness.requests.ConnectionError("probe network unavailable"),
        ]
    )

    def fake_get(*_args, **_kwargs):
        response = next(responses)
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(readiness.requests, "get", fake_get)

    with pytest.raises(RuntimeError, match="authentication probe"):
        readiness.wait_for_sandbox_ready(
            "http://sandbox",
            timeout=5,
            api_key="expected-token",
        )


class _FakeAsyncClient:
    def __init__(self, *, responses: list[object], calls: list[str], timeout: float, request_timeouts: list[float] | None = None, request_headers: list[dict[str, str] | None] | None = None) -> None:
        self._responses = responses
        self._calls = calls
        self._timeout = timeout
        self._request_timeouts = request_timeouts
        self._request_headers = request_headers

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str, *, timeout: float, headers: dict[str, str] | None = None):
        self._calls.append(url)
        if self._request_headers is not None:
            self._request_headers.append(headers)
        if self._request_timeouts is not None:
            self._request_timeouts.append(timeout)
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class _FakeLoop:
    def __init__(self, times: list[float]) -> None:
        self._times = times
        self._index = 0

    def time(self) -> float:
        value = self._times[self._index]
        self._index += 1
        return value


@pytest.mark.anyio
async def test_wait_for_sandbox_ready_async_sends_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    headers: list[dict[str, str] | None] = []

    def fake_client(*, timeout: float):
        return _FakeAsyncClient(
            responses=[_response(200), _response(401), _response(403)],
            calls=[],
            timeout=timeout,
            request_headers=headers,
        )

    monkeypatch.setattr(readiness.httpx, "AsyncClient", fake_client)

    assert await readiness.wait_for_sandbox_ready_async("http://sandbox", timeout=5, api_key="expected-token") is True
    assert headers[0] == {"X-AIO-API-Key": "expected-token"}
    assert headers[1] is None
    assert headers[2]
    assert headers[2]["X-AIO-API-Key"] != "expected-token"


@pytest.mark.anyio
async def test_wait_for_sandbox_ready_async_rejects_image_that_ignores_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_client(*, timeout: float):
        return _FakeAsyncClient(
            responses=[_response(200), _response(200)],
            calls=[],
            timeout=timeout,
        )

    monkeypatch.setattr(readiness.httpx, "AsyncClient", fake_client)

    assert await readiness.wait_for_sandbox_ready_async("http://sandbox", timeout=5, api_key="expected-token") is False


@pytest.mark.anyio
async def test_wait_for_sandbox_ready_async_surfaces_negative_auth_probe_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_client(*, timeout: float):
        return _FakeAsyncClient(
            responses=[
                _response(200),
                readiness.httpx.ConnectError("probe network unavailable"),
            ],
            calls=[],
            timeout=timeout,
        )

    monkeypatch.setattr(readiness.httpx, "AsyncClient", fake_client)

    with pytest.raises(RuntimeError, match="authentication probe"):
        await readiness.wait_for_sandbox_ready_async(
            "http://sandbox",
            timeout=5,
            api_key="expected-token",
        )


@pytest.mark.anyio
async def test_wait_for_sandbox_ready_async_uses_nonblocking_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    sleeps: list[float] = []

    def fake_client(*, timeout: float):
        return _FakeAsyncClient(
            responses=[SimpleNamespace(status_code=503), SimpleNamespace(status_code=200)],
            calls=calls,
            timeout=timeout,
        )

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(readiness.httpx, "AsyncClient", fake_client)
    monkeypatch.setattr(readiness.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(readiness.requests, "get", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("requests.get should not be used")))
    monkeypatch.setattr(readiness.time, "sleep", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("time.sleep should not be used")))

    assert await readiness.wait_for_sandbox_ready_async("http://sandbox", timeout=5, poll_interval=0.05) is True

    assert calls == ["http://sandbox/v1/sandbox", "http://sandbox/v1/sandbox"]
    assert sleeps == [0.05]


@pytest.mark.anyio
async def test_wait_for_sandbox_ready_async_retries_request_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    sleeps: list[float] = []

    def fake_client(*, timeout: float):
        return _FakeAsyncClient(
            responses=[readiness.httpx.ConnectError("not ready"), SimpleNamespace(status_code=200)],
            calls=calls,
            timeout=timeout,
        )

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(readiness.httpx, "AsyncClient", fake_client)
    monkeypatch.setattr(readiness.asyncio, "sleep", fake_sleep)

    assert await readiness.wait_for_sandbox_ready_async("http://sandbox", timeout=5, poll_interval=0.01) is True

    assert len(calls) == 2
    assert sleeps == [0.01]


@pytest.mark.anyio
async def test_wait_for_sandbox_ready_async_clamps_request_and_sleep_to_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    request_timeouts: list[float] = []
    sleeps: list[float] = []

    def fake_client(*, timeout: float):
        return _FakeAsyncClient(
            responses=[SimpleNamespace(status_code=503)],
            calls=calls,
            timeout=timeout,
            request_timeouts=request_timeouts,
        )

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(readiness.httpx, "AsyncClient", fake_client)
    monkeypatch.setattr(readiness.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(readiness.asyncio, "get_running_loop", lambda: _FakeLoop([100.0, 100.5, 101.75, 102.0]))

    assert await readiness.wait_for_sandbox_ready_async("http://sandbox", timeout=2, poll_interval=1.0) is False

    assert calls == ["http://sandbox/v1/sandbox"]
    assert request_timeouts == [1.5]
    assert sleeps == [0.25]
