import asyncio
import os
import shutil
import threading
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import anyio
import pytest
from _router_auth_helpers import call_unwrapped, make_authed_test_app
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import FileResponse

import app.gateway.routers.artifacts as artifacts_router
from app.gateway.internal_auth import INTERNAL_OWNER_USER_ID_HEADER_NAME, INTERNAL_SYSTEM_ROLE

ACTIVE_ARTIFACT_CASES = [
    ("poc.html", "<html><body><script>alert('xss')</script></body></html>"),
    ("page.xhtml", '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>hello</body></html>'),
    ("image.svg", '<svg xmlns="http://www.w3.org/2000/svg"><script>alert("xss")</script></svg>'),
]


def _make_request(query_string: bytes = b"") -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": query_string})


def test_get_artifact_reads_utf8_text_file_on_windows_locale(tmp_path, monkeypatch) -> None:
    artifact_path = tmp_path / "note.txt"
    text = "Curly quotes: \u201cutf8\u201d"
    artifact_path.write_text(text, encoding="utf-8")

    original_read_text = Path.read_text

    def read_text_with_gbk_default(self, *args, **kwargs):
        kwargs.setdefault("encoding", "gbk")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text_with_gbk_default)
    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, **_kwargs: artifact_path)

    request = _make_request()
    response = asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", "mnt/user-data/outputs/note.txt", request))

    assert bytes(response.body).decode("utf-8") == text
    assert response.media_type == "text/plain"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_get_artifact_keeps_opened_file_when_path_is_swapped_to_symlink(tmp_path, monkeypatch) -> None:
    artifact_path = tmp_path / "note.txt"
    artifact_path.write_text("safe artifact", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside secret", encoding="utf-8")
    original_guess_type = artifacts_router.mimetypes.guess_type

    def swap_after_validation(path):
        artifact_path.unlink()
        artifact_path.symlink_to(outside)
        return original_guess_type(path)

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, **_kwargs: artifact_path)
    monkeypatch.setattr(artifacts_router.mimetypes, "guess_type", swap_after_validation)

    response = asyncio.run(
        call_unwrapped(
            artifacts_router.get_artifact,
            "thread-1",
            "mnt/user-data/outputs/note.txt",
            _make_request(),
        )
    )

    assert bytes(response.body) == b"safe artifact"


def test_get_artifact_rejects_ancestor_symlink_swap_after_resolution(tmp_path, monkeypatch) -> None:
    outputs_dir = tmp_path / "outputs"
    switch_dir = outputs_dir / "switch"
    switch_dir.mkdir(parents=True)
    artifact_path = switch_dir / "note.txt"
    artifact_path.write_text("safe artifact", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "note.txt").write_text("outside secret", encoding="utf-8")

    def resolve_then_swap(_thread_id, _path, **_kwargs):
        checked_path = artifact_path
        shutil.rmtree(switch_dir)
        switch_dir.symlink_to(outside, target_is_directory=True)
        return checked_path

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", resolve_then_swap)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            call_unwrapped(
                artifacts_router.get_artifact,
                "thread-1",
                "mnt/user-data/outputs/switch/note.txt",
                _make_request(),
            )
        )

    assert exc_info.value.status_code == 400
    assert (outside / "note.txt").read_text(encoding="utf-8") == "outside secret"


def test_get_artifact_uses_trusted_internal_owner_header(tmp_path, monkeypatch) -> None:
    import deerflow.config.paths as paths_mod

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr(paths_mod, "_paths", None)

    owner_outputs = tmp_path / "users" / "owner-artifact" / "threads" / "thread-owned" / "user-data" / "outputs"
    owner_outputs.mkdir(parents=True)
    (owner_outputs / "owner.txt").write_text("owner artifact", encoding="utf-8")

    default_outputs = tmp_path / "users" / "default" / "threads" / "thread-owned" / "user-data" / "outputs"
    default_outputs.mkdir(parents=True)
    (default_outputs / "owner.txt").write_text("default artifact", encoding="utf-8")

    request = _make_request()
    request.state.user = type("InternalUser", (), {"id": "default", "system_role": INTERNAL_SYSTEM_ROLE})()
    request.scope["headers"] = [(INTERNAL_OWNER_USER_ID_HEADER_NAME.lower().encode(), b"owner-artifact")]

    response = asyncio.run(
        call_unwrapped(
            artifacts_router.get_artifact,
            "thread-owned",
            "mnt/user-data/outputs/owner.txt",
            request,
        )
    )

    assert bytes(response.body).decode("utf-8") == "owner artifact"


def test_get_artifact_uses_request_user_bucket(tmp_path, monkeypatch) -> None:
    import deerflow.config.paths as paths_mod

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr(paths_mod, "_paths", None)

    alice_outputs = tmp_path / "users" / "alice" / "threads" / "shared-thread" / "user-data" / "outputs"
    alice_outputs.mkdir(parents=True)
    (alice_outputs / "report.txt").write_text("alice artifact", encoding="utf-8")

    bob_outputs = tmp_path / "users" / "bob" / "threads" / "shared-thread" / "user-data" / "outputs"
    bob_outputs.mkdir(parents=True)
    (bob_outputs / "report.txt").write_text("bob artifact", encoding="utf-8")

    request = _make_request()
    request.state.user = type("User", (), {"id": "alice", "system_role": "user"})()

    response = asyncio.run(
        call_unwrapped(
            artifacts_router.get_artifact,
            "shared-thread",
            "mnt/user-data/outputs/report.txt",
            request,
        )
    )

    assert bytes(response.body).decode("utf-8") == "alice artifact"


@pytest.mark.parametrize(("filename", "content"), ACTIVE_ARTIFACT_CASES)
def test_get_artifact_forces_download_for_active_content(tmp_path, monkeypatch, filename: str, content: str) -> None:
    artifact_path = tmp_path / filename
    artifact_path.write_text(content, encoding="utf-8")

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, **_kwargs: artifact_path)

    response = asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", f"mnt/user-data/outputs/{filename}", _make_request()))

    assert isinstance(response, FileResponse)
    assert response.headers.get("content-disposition", "").startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(("filename", "content"), ACTIVE_ARTIFACT_CASES)
def test_get_artifact_forces_download_for_active_content_in_skill_archive(tmp_path, monkeypatch, filename: str, content: str) -> None:
    skill_path = tmp_path / "sample.skill"
    with zipfile.ZipFile(skill_path, "w") as zip_ref:
        zip_ref.writestr(filename, content)

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, **_kwargs: skill_path)

    response = asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", f"mnt/user-data/outputs/sample.skill/{filename}", _make_request()))

    assert response.headers.get("content-disposition", "").startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert bytes(response.body) == content.encode("utf-8")


def test_get_artifact_forces_download_for_unknown_binary(tmp_path, monkeypatch) -> None:
    artifact_path = tmp_path / "blob.unknown"
    payload = b"\x00\x01\x02binary"
    artifact_path.write_bytes(payload)

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, **_kwargs: artifact_path)

    response = asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", "mnt/user-data/outputs/blob.unknown", _make_request()))

    assert bytes(response.body) == payload
    assert response.media_type == "application/octet-stream"
    assert response.headers.get("content-disposition", "").startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_get_artifact_download_streams_without_payload_read(tmp_path, monkeypatch) -> None:
    artifact_path = tmp_path / "large.bin"
    artifact_path.write_bytes(b"\x00" * 1024)

    def fail_read(*_args, **_kwargs):
        raise AssertionError("download responses must stream with FileResponse")

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, **_kwargs: artifact_path)
    monkeypatch.setattr(Path, "read_bytes", fail_read)
    monkeypatch.setattr(Path, "read_text", fail_read)

    response = asyncio.run(
        call_unwrapped(
            artifacts_router.get_artifact,
            "thread-1",
            "mnt/user-data/outputs/large.bin",
            _make_request(),
            download=True,
        )
    )

    assert isinstance(response, FileResponse)
    assert response.headers.get("content-disposition", "").startswith("attachment;")


@pytest.mark.asyncio
async def test_cancelled_artifact_open_closes_worker_file_descriptor(tmp_path, monkeypatch) -> None:
    artifact_path = tmp_path / "large.bin"
    artifact_path.write_bytes(b"payload")
    opened: dict[str, int] = {}
    started = threading.Event()
    release = threading.Event()
    original_reader = artifacts_router._read_artifact_payload

    def slow_reader(*args, **kwargs):
        result = original_reader(*args, **kwargs)
        opened["fd"] = result[2][0]
        started.set()
        release.wait(1.0)
        return result

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, **_kwargs: artifact_path)
    monkeypatch.setattr(artifacts_router, "_read_artifact_payload", slow_reader)
    task = asyncio.create_task(
        call_unwrapped(
            artifacts_router.get_artifact,
            "thread-1",
            "mnt/user-data/outputs/large.bin",
            _make_request(),
            download=True,
        )
    )
    await asyncio.to_thread(started.wait, 1.0)
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    fd = opened["fd"]
    try:
        for _ in range(100):
            try:
                os.fstat(fd)
            except OSError:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("cancelled artifact read leaked its file descriptor")
    finally:
        artifacts_router._close_fd(fd)


@pytest.mark.parametrize(
    ("method", "headers"),
    [
        ("HEAD", []),
        ("GET", [(b"range", b"bytes=0-2")]),
    ],
)
@pytest.mark.asyncio
async def test_artifact_response_closes_fd_for_head_and_range(tmp_path, monkeypatch, method, headers) -> None:
    artifact_path = tmp_path / "download.bin"
    artifact_path.write_bytes(b"payload")
    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, **_kwargs: artifact_path)
    response = await call_unwrapped(
        artifacts_router.get_artifact,
        "thread-1",
        "mnt/user-data/outputs/download.bin",
        _make_request(),
        download=True,
    )
    fd = response.path
    messages = []

    async def send(message):
        messages.append(message)

    await response(
        {"type": "http", "method": method, "headers": headers, "extensions": {}},
        AsyncMock(),
        send,
    )

    with pytest.raises(OSError):
        os.fstat(fd)
    assert messages


@pytest.mark.asyncio
async def test_artifact_response_closes_fd_when_client_send_fails(tmp_path, monkeypatch) -> None:
    artifact_path = tmp_path / "download.bin"
    artifact_path.write_bytes(b"payload")
    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, **_kwargs: artifact_path)
    response = await call_unwrapped(
        artifacts_router.get_artifact,
        "thread-1",
        "mnt/user-data/outputs/download.bin",
        _make_request(),
        download=True,
    )
    fd = response.path

    async def broken_send(message):
        if message["type"] == "http.response.body":
            raise ConnectionError("client disconnected")

    with pytest.raises(ConnectionError):
        await response(
            {"type": "http", "method": "GET", "headers": [], "extensions": {}},
            AsyncMock(),
            broken_send,
        )

    with pytest.raises(OSError):
        os.fstat(fd)


@pytest.mark.asyncio
async def test_artifact_response_does_not_close_reused_file_descriptor(tmp_path, monkeypatch) -> None:
    artifact_path = tmp_path / "download.bin"
    artifact_path.write_bytes(b"payload")
    replacement_path = tmp_path / "replacement.bin"
    replacement_path.write_bytes(b"replacement")
    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, **_kwargs: artifact_path)
    response = await call_unwrapped(
        artifacts_router.get_artifact,
        "thread-1",
        "mnt/user-data/outputs/download.bin",
        _make_request(),
        download=True,
    )
    stream_fd: int | None = None
    replacement_fd: int | None = None
    original_aclose = anyio.AsyncFile.aclose

    async def close_then_reuse_fd(async_file) -> None:
        nonlocal replacement_fd, stream_fd
        stream_fd = async_file.wrapped.fileno()
        await original_aclose(async_file)
        replacement_fd = os.open(replacement_path, os.O_RDONLY)

    monkeypatch.setattr(anyio.AsyncFile, "aclose", close_then_reuse_fd)

    async def send(_message) -> None:
        pass

    try:
        await response(
            {"type": "http", "method": "GET", "headers": [], "extensions": {}},
            AsyncMock(),
            send,
        )

        assert replacement_fd == stream_fd
        assert os.read(replacement_fd, len(b"replacement")) == b"replacement"
    finally:
        if replacement_fd is not None:
            artifacts_router._close_fd(replacement_fd)


def test_get_artifact_forces_download_for_unknown_binary_in_skill_archive(tmp_path, monkeypatch) -> None:
    payload = b"\x00\x01\x02binary"
    skill_path = tmp_path / "sample.skill"
    with zipfile.ZipFile(skill_path, "w") as zip_ref:
        zip_ref.writestr("blob.unknown", payload)

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, **_kwargs: skill_path)

    response = asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", "mnt/user-data/outputs/sample.skill/blob.unknown", _make_request()))

    assert bytes(response.body) == payload
    assert response.media_type == "application/octet-stream"
    assert response.headers.get("content-disposition", "").startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_get_artifact_download_false_does_not_force_attachment(tmp_path, monkeypatch) -> None:
    artifact_path = tmp_path / "note.txt"
    artifact_path.write_text("hello", encoding="utf-8")

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, **_kwargs: artifact_path)

    app = make_authed_test_app()
    app.include_router(artifacts_router.router)

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/artifacts/mnt/user-data/outputs/note.txt?download=false")

    assert response.status_code == 200
    assert response.text == "hello"
    assert "content-disposition" not in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"


def test_get_artifact_download_true_forces_attachment_for_skill_archive(tmp_path, monkeypatch) -> None:
    skill_path = tmp_path / "sample.skill"
    with zipfile.ZipFile(skill_path, "w") as zip_ref:
        zip_ref.writestr("notes.txt", "hello")

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, **_kwargs: skill_path)

    app = make_authed_test_app()
    app.include_router(artifacts_router.router)

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/artifacts/mnt/user-data/outputs/sample.skill/notes.txt?download=true")

    assert response.status_code == 200
    assert response.text == "hello"
    assert response.headers.get("content-disposition", "").startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_skill_archive_preview_rejects_oversized_member_before_decompression(tmp_path) -> None:
    skill_path = tmp_path / "sample.skill"
    payload = b"A" * (artifacts_router.MAX_SKILL_ARCHIVE_MEMBER_BYTES + 1)
    with zipfile.ZipFile(skill_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zip_ref:
        zip_ref.writestr("SKILL.md", payload)

    assert skill_path.stat().st_size < artifacts_router.MAX_SKILL_ARCHIVE_MEMBER_BYTES

    with pytest.raises(HTTPException) as exc_info:
        artifacts_router._extract_file_from_skill_archive(skill_path, "SKILL.md")

    assert exc_info.value.status_code == 413
