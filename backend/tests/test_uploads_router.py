import asyncio
import os
import stat
import threading
import time
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from _router_auth_helpers import call_unwrapped, make_authed_test_app
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient

from app.gateway.deps import get_config
from app.gateway.internal_auth import INTERNAL_OWNER_USER_ID_HEADER_NAME, INTERNAL_SYSTEM_ROLE
from app.gateway.routers import uploads


class ChunkedUpload:
    def __init__(self, filename: str, chunks: list[bytes]):
        self.filename = filename
        self._chunks = list(chunks)
        self.read_calls: list[int | None] = []

    async def read(self, size: int | None = None) -> bytes:
        self.read_calls.append(size)
        if size is None:
            raise AssertionError("upload must be read with an explicit chunk size")
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


class CancelledChunkedUpload(ChunkedUpload):
    async def read(self, size: int | None = None) -> bytes:
        if self._chunks:
            return await super().read(size)
        raise asyncio.CancelledError


def _mounted_provider() -> MagicMock:
    provider = MagicMock()
    provider.uses_thread_data_mounts = True
    return provider


def _request_for_user(user_id: str) -> SimpleNamespace:
    return SimpleNamespace(headers={}, state=SimpleNamespace(user=SimpleNamespace(id=user_id, system_role="user")))


def test_upload_files_writes_thread_storage_and_skips_local_sandbox_sync(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = True
    provider.acquire.return_value = "local"
    sandbox = MagicMock()
    provider.get.return_value = sandbox

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        file = UploadFile(filename="notes.txt", file=BytesIO(b"hello uploads"))
        result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-local", request=MagicMock(), files=[file], config=SimpleNamespace()))

    assert result.success is True
    assert len(result.files) == 1
    assert result.files[0].filename == "notes.txt"
    assert result.files[0].size == len(b"hello uploads")
    assert (thread_uploads_dir / "notes.txt").read_bytes() == b"hello uploads"

    sandbox.update_file.assert_not_called()


def test_upload_files_requires_existing_owned_thread():
    app = make_authed_test_app(owner_check_passes=False)
    config = MagicMock()
    config.uploads = {}
    app.state.config = config
    app.dependency_overrides[get_config] = lambda: config
    app.include_router(uploads.router)

    with TestClient(app) as client:
        response = client.post(
            "/api/threads/thread-local/uploads",
            files=[("files", ("notes.txt", b"hello", "text/plain"))],
        )

    assert response.status_code == 404
    app.state.thread_store.check_access.assert_awaited_once()
    assert app.state.thread_store.check_access.await_args.kwargs["require_existing"] is True


def test_upload_route_registers_thread_write_guard(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    provider = _mounted_provider()
    app = make_authed_test_app()
    config = MagicMock()
    config.uploads = {}
    app.state.config = config
    app.dependency_overrides[get_config] = lambda: config
    app.include_router(uploads.router)
    begin_write = AsyncMock(wraps=app.state.run_manager.begin_thread_write)
    end_write = AsyncMock(wraps=app.state.run_manager.end_thread_write)
    app.state.run_manager.begin_thread_write = begin_write
    app.state.run_manager.end_thread_write = end_write

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        TestClient(app) as client,
    ):
        response = client.post(
            "/api/threads/thread-local/uploads",
            files=[("files", ("notes.txt", b"hello", "text/plain"))],
        )

    assert response.status_code == 200
    begin_write.assert_awaited_once_with("thread-local")
    end_write.assert_awaited_once_with("thread-local")


def test_upload_files_writes_ocr_sidecar_for_images(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = _mounted_provider()

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch.object(uploads, "extract_image_text", return_value="图片文字"),
    ):
        file = UploadFile(filename="screenshot.png", file=BytesIO(b"png"))
        result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-local", request=MagicMock(), files=[file], config=SimpleNamespace()))

    assert result.success is True
    assert result.files[0].ocr_file == "screenshot.png.ocr.txt"
    assert (thread_uploads_dir / "screenshot.png.ocr.txt").read_text(encoding="utf-8") == "图片文字"


def test_upload_and_list_response_models_expose_size_as_int(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    (thread_uploads_dir / "notes.txt").write_bytes(b"hello uploads")

    paths = MagicMock()
    paths.sandbox_uploads_dir.return_value = thread_uploads_dir

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_paths", return_value=paths),
    ):
        result = asyncio.run(call_unwrapped(uploads.list_uploaded_files, "thread-local", request=MagicMock()))

    assert result.count == 1
    assert result.files[0].filename == "notes.txt"
    assert result.files[0].size == len(b"hello uploads")


@pytest.mark.asyncio
async def test_list_uploaded_files_does_not_block_gateway_event_loop(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    paths = MagicMock()
    paths.sandbox_uploads_dir.return_value = thread_uploads_dir

    def slow_list(_uploads_dir):
        time.sleep(0.2)
        return {"files": [], "count": 0}

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "list_files_in_dir", side_effect=slow_list),
        patch.object(uploads, "get_paths", return_value=paths),
    ):
        started = time.perf_counter()
        task = asyncio.create_task(
            call_unwrapped(
                uploads.list_uploaded_files,
                "thread-local",
                request=MagicMock(),
            )
        )
        await asyncio.sleep(0.02)
        tick_elapsed = time.perf_counter() - started
        await task

    assert tick_elapsed < 0.1


@pytest.mark.asyncio
async def test_same_thread_list_waits_for_inflight_upload(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    upload_started = asyncio.Event()
    release_upload = asyncio.Event()
    paths = MagicMock()
    paths.sandbox_uploads_dir.return_value = thread_uploads_dir

    class PausedUpload:
        filename = "same.txt"

        def __init__(self):
            self._sent = False

        async def read(self, _size):
            if self._sent:
                return b""
            self._sent = True
            upload_started.set()
            await release_upload.wait()
            return b"complete-upload"

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_paths", return_value=paths),
        patch.object(uploads, "get_sandbox_provider", return_value=_mounted_provider()),
    ):
        upload_task = asyncio.create_task(
            call_unwrapped(
                uploads.upload_files,
                "thread-local",
                request=MagicMock(),
                files=[PausedUpload()],
                config=SimpleNamespace(),
            )
        )
        await upload_started.wait()
        list_task = asyncio.create_task(
            call_unwrapped(
                uploads.list_uploaded_files,
                "thread-local",
                request=MagicMock(),
            )
        )
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(list_task), timeout=0.05)
        release_upload.set()
        await upload_task
        result = await list_task

    assert result.count == 1
    assert result.files[0].filename == "same.txt"
    assert result.files[0].size == len(b"complete-upload")


def test_upload_openapi_schema_exposes_file_size_as_integer():
    upload_schema = uploads.UploadResponse.model_json_schema()
    list_schema = uploads.UploadListResponse.model_json_schema()

    assert upload_schema["$defs"]["UploadedFileInfo"]["properties"]["size"]["type"] == "integer"
    assert list_schema["$defs"]["UploadedFileInfo"]["properties"]["size"]["type"] == "integer"


def test_list_uploaded_files_uses_trusted_internal_owner_header(tmp_path, monkeypatch):
    import deerflow.config.paths as paths_mod

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr(paths_mod, "_paths", None)

    owner_uploads = tmp_path / "users" / "owner-upload" / "threads" / "thread-owned" / "user-data" / "uploads"
    owner_uploads.mkdir(parents=True)
    (owner_uploads / "owner.txt").write_text("owner", encoding="utf-8")

    default_uploads = tmp_path / "users" / "default" / "threads" / "thread-owned" / "user-data" / "uploads"
    default_uploads.mkdir(parents=True)
    (default_uploads / "default.txt").write_text("default", encoding="utf-8")

    request = SimpleNamespace(
        headers={INTERNAL_OWNER_USER_ID_HEADER_NAME: "owner-upload"},
        state=SimpleNamespace(user=SimpleNamespace(id="default", system_role=INTERNAL_SYSTEM_ROLE)),
    )

    result = asyncio.run(call_unwrapped(uploads.list_uploaded_files, "thread-owned", request=request))

    assert [item.filename for item in result.files] == ["owner.txt"]


def test_upload_files_auto_renames_duplicate_form_filenames(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = True

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        result = asyncio.run(
            call_unwrapped(
                uploads.upload_files,
                "thread-local",
                request=MagicMock(),
                files=[
                    UploadFile(filename="data.txt", file=BytesIO(b"first")),
                    UploadFile(filename="data.txt", file=BytesIO(b"second")),
                ],
                config=SimpleNamespace(),
            )
        )

    assert result.success is True
    assert [file_info.filename for file_info in result.files] == ["data.txt", "data_1.txt"]
    assert result.files[0].original_filename is None
    assert result.files[1].original_filename == "data.txt"
    assert (thread_uploads_dir / "data.txt").read_bytes() == b"first"
    assert (thread_uploads_dir / "data_1.txt").read_bytes() == b"second"


def test_upload_files_skips_acquire_when_thread_data_is_mounted(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = True

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        file = UploadFile(filename="notes.txt", file=BytesIO(b"hello uploads"))
        result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-mounted", request=MagicMock(), files=[file], config=SimpleNamespace()))

    assert result.success is True
    assert (thread_uploads_dir / "notes.txt").read_bytes() == b"hello uploads"
    provider.acquire.assert_not_called()
    provider.get.assert_not_called()


def test_upload_files_does_not_auto_convert_documents_by_default(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = True
    provider.acquire.return_value = "local"
    sandbox = MagicMock()
    provider.get.return_value = sandbox

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch.object(uploads, "_auto_convert_documents_enabled", return_value=False),
        patch.object(uploads, "convert_file_to_markdown", AsyncMock()) as convert_mock,
    ):
        file = UploadFile(filename="report.pdf", file=BytesIO(b"pdf-bytes"))
        result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-local", request=MagicMock(), files=[file], config=SimpleNamespace()))

    assert result.success is True
    assert len(result.files) == 1
    assert result.files[0].filename == "report.pdf"
    assert result.files[0].markdown_file is None
    convert_mock.assert_not_called()
    assert not (thread_uploads_dir / "report.md").exists()


def test_upload_files_syncs_non_local_sandbox_and_marks_markdown_file(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = False
    provider.acquire.return_value = "aio-1"
    sandbox = MagicMock()
    provider.get.return_value = sandbox

    async def fake_convert(file_path: Path) -> Path:
        md_path = file_path.with_suffix(".md")
        md_path.write_text("converted", encoding="utf-8")
        return md_path

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch.object(uploads, "_auto_convert_documents_enabled", return_value=True),
        patch.object(uploads, "convert_file_to_markdown", AsyncMock(side_effect=fake_convert)),
    ):
        file = UploadFile(filename="report.pdf", file=BytesIO(b"pdf-bytes"))
        result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-aio", request=MagicMock(), files=[file], config=SimpleNamespace()))

    assert result.success is True
    assert len(result.files) == 1
    file_info = result.files[0]
    assert file_info.filename == "report.pdf"
    assert file_info.markdown_file == "report.md"

    assert (thread_uploads_dir / "report.pdf").read_bytes() == b"pdf-bytes"
    assert (thread_uploads_dir / "report.md").read_text(encoding="utf-8") == "converted"

    sandbox.update_file.assert_any_call("/mnt/user-data/uploads/report.pdf", b"pdf-bytes")
    sandbox.update_file.assert_any_call("/mnt/user-data/uploads/report.md", b"converted")


def test_upload_files_makes_non_local_files_sandbox_writable(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = False
    provider.acquire.return_value = "aio-1"
    sandbox = MagicMock()
    provider.get.return_value = sandbox

    async def fake_convert(file_path: Path) -> Path:
        md_path = file_path.with_suffix(".md")
        md_path.write_text("converted", encoding="utf-8")
        return md_path

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch.object(uploads, "_auto_convert_documents_enabled", return_value=True),
        patch.object(uploads, "convert_file_to_markdown", AsyncMock(side_effect=fake_convert)),
        patch.object(uploads, "_make_file_sandbox_writable") as make_writable,
    ):
        file = UploadFile(filename="report.pdf", file=BytesIO(b"pdf-bytes"))
        result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-aio", request=MagicMock(), files=[file], config=SimpleNamespace()))

    assert result.success is True
    make_writable.assert_any_call(
        thread_uploads_dir / "report.pdf",
        directory_fd=ANY,
    )
    make_writable.assert_any_call(
        thread_uploads_dir / "report.md",
        directory_fd=ANY,
    )


def test_upload_files_does_not_adjust_permissions_for_local_sandbox(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = True
    provider.needs_upload_permission_adjustment = False
    provider.acquire.return_value = "local"
    sandbox = MagicMock()
    provider.get.return_value = sandbox

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch.object(uploads, "_make_file_sandbox_writable") as make_writable,
        patch.object(uploads, "_make_file_sandbox_readable") as make_readable,
    ):
        file = UploadFile(filename="notes.txt", file=BytesIO(b"hello uploads"))
        result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-local", request=MagicMock(), files=[file], config=SimpleNamespace()))

    assert result.success is True
    make_writable.assert_not_called()
    make_readable.assert_not_called()


def test_upload_files_acquires_non_local_sandbox_before_writing(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = False
    sandbox = MagicMock()
    provider.get.return_value = sandbox

    def acquire_before_writes(thread_id: str, *, user_id: str | None = None) -> str:
        assert list(thread_uploads_dir.iterdir()) == []
        assert user_id == "owner-upload"
        return "aio-1"

    provider.acquire.side_effect = acquire_before_writes

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        file = UploadFile(filename="notes.txt", file=BytesIO(b"hello uploads"))
        result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-aio", request=_request_for_user("owner-upload"), files=[file], config=SimpleNamespace()))

    assert result.success is True
    provider.acquire.assert_called_once_with("thread-aio", user_id="owner-upload")
    sandbox.update_file.assert_called_once_with("/mnt/user-data/uploads/notes.txt", b"hello uploads")


def test_upload_files_fails_before_writing_when_non_local_sandbox_unavailable(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = False
    provider.acquire.side_effect = RuntimeError("sandbox unavailable")
    file = ChunkedUpload("notes.txt", [b"hello uploads"])

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        with pytest.raises(RuntimeError, match="sandbox unavailable"):
            asyncio.run(call_unwrapped(uploads.upload_files, "thread-aio", request=MagicMock(), files=[file], config=SimpleNamespace()))

    assert list(thread_uploads_dir.iterdir()) == []
    assert file.read_calls == []
    provider.get.assert_not_called()


def test_upload_files_rejects_too_many_files_before_writing(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=_mounted_provider()),
        patch.object(uploads, "_get_upload_limits", return_value=uploads.UploadLimits(max_files=1, max_file_size=10, max_total_size=20)),
    ):
        files = [
            ChunkedUpload("one.txt", [b"one"]),
            ChunkedUpload("two.txt", [b"two"]),
        ]
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(call_unwrapped(uploads.upload_files, "thread-local", request=MagicMock(), files=files, config=SimpleNamespace()))

    assert exc_info.value.status_code == 413
    assert list(thread_uploads_dir.iterdir()) == []
    assert files[0].read_calls == []
    assert files[1].read_calls == []


def test_upload_files_rejects_oversized_single_file_and_removes_partial_file(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = _mounted_provider()
    file = ChunkedUpload("big.txt", [b"123456"])

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch.object(uploads, "_get_upload_limits", return_value=uploads.UploadLimits(max_files=10, max_file_size=5, max_total_size=20)),
    ):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(call_unwrapped(uploads.upload_files, "thread-local", request=MagicMock(), files=[file], config=SimpleNamespace()))

    assert exc_info.value.status_code == 413
    assert not (thread_uploads_dir / "big.txt").exists()
    assert file.read_calls == [8192]
    provider.acquire.assert_not_called()


def test_upload_files_rejects_total_size_over_limit_and_cleans_request_files(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=_mounted_provider()),
        patch.object(uploads, "_get_upload_limits", return_value=uploads.UploadLimits(max_files=10, max_file_size=10, max_total_size=5)),
    ):
        files = [
            ChunkedUpload("first.txt", [b"123"]),
            ChunkedUpload("second.txt", [b"456"]),
        ]
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(call_unwrapped(uploads.upload_files, "thread-local", request=MagicMock(), files=files, config=SimpleNamespace()))

    assert exc_info.value.status_code == 413
    assert not (thread_uploads_dir / "first.txt").exists()
    assert not (thread_uploads_dir / "second.txt").exists()


def test_upload_failure_restores_existing_file_overwritten_earlier_in_request(
    tmp_path,
):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    existing = thread_uploads_dir / "notes.txt"
    existing.write_bytes(b"old upload")

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=_mounted_provider()),
        patch.object(
            uploads,
            "_get_upload_limits",
            return_value=uploads.UploadLimits(
                max_files=10,
                max_file_size=10,
                max_total_size=5,
            ),
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        asyncio.run(
            call_unwrapped(
                uploads.upload_files,
                "thread-local",
                request=MagicMock(),
                files=[
                    ChunkedUpload("notes.txt", [b"new"]),
                    ChunkedUpload("second.txt", [b"456"]),
                ],
                config=SimpleNamespace(),
            )
        )

    assert exc_info.value.status_code == 413
    assert existing.read_bytes() == b"old upload"
    assert not (thread_uploads_dir / "second.txt").exists()


def test_failed_backup_restore_retains_original_recovery_copy(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    existing = thread_uploads_dir / "notes.txt"
    existing.write_bytes(b"old upload")
    real_replace = uploads.os.replace

    def fail_restore(src, dst, **kwargs):
        if str(src) == "0" and str(dst) == existing.name and kwargs.get("src_dir_fd") != kwargs.get("dst_dir_fd"):
            raise OSError("restore blocked")
        return real_replace(src, dst, **kwargs)

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=_mounted_provider()),
        patch.object(uploads.os, "replace", side_effect=fail_restore),
        patch.object(
            uploads,
            "_get_upload_limits",
            return_value=uploads.UploadLimits(max_files=10, max_file_size=10, max_total_size=5),
        ),
        pytest.raises(HTTPException),
    ):
        asyncio.run(
            call_unwrapped(
                uploads.upload_files,
                "thread-local",
                request=MagicMock(),
                files=[
                    ChunkedUpload("notes.txt", [b"new"]),
                    ChunkedUpload("second.txt", [b"456"]),
                ],
                config=SimpleNamespace(),
            )
        )

    recovery_files = [path for path in thread_uploads_dir.glob(".deerflow-upload-backup-*/*") if path.name != uploads._RESTORE_FAILURE_MARKER]
    assert len(recovery_files) == 1
    assert recovery_files[0].read_bytes() == b"old upload"


@pytest.mark.asyncio
async def test_cancelled_new_upload_removes_partial_file(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=_mounted_provider()),
        pytest.raises(asyncio.CancelledError),
    ):
        await call_unwrapped(
            uploads.upload_files,
            "thread-local",
            request=MagicMock(),
            files=[CancelledChunkedUpload("partial.txt", [b"partial"])],
            config=SimpleNamespace(),
        )

    assert not (thread_uploads_dir / "partial.txt").exists()
    assert not list(thread_uploads_dir.glob(".deerflow-upload-backup-*"))


@pytest.mark.asyncio
async def test_cancelled_ocr_sidecar_write_cannot_overwrite_restored_file(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    image = thread_uploads_dir / "image.png"
    sidecar = thread_uploads_dir / "image.png.ocr.txt"
    image.write_bytes(b"old image")
    sidecar.write_bytes(b"old ocr")
    write_started = threading.Event()
    allow_write = threading.Event()
    write_finished = threading.Event()
    original_write = uploads.write_upload_file_no_symlink

    def slow_sidecar_write(base_dir, filename, data, **kwargs):
        write_started.set()
        allow_write.wait(1.0)
        try:
            return original_write(base_dir, filename, data, **kwargs)
        finally:
            write_finished.set()

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=_mounted_provider()),
        patch.object(uploads, "extract_image_text", return_value="new ocr"),
        patch.object(uploads, "write_upload_file_no_symlink", side_effect=slow_sidecar_write),
    ):
        task = asyncio.create_task(
            call_unwrapped(
                uploads.upload_files,
                "thread-local",
                request=MagicMock(),
                files=[UploadFile(filename="image.png", file=BytesIO(b"new image"))],
                config=SimpleNamespace(),
            )
        )
        await asyncio.to_thread(write_started.wait, 1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert image.read_bytes() == b"old image"
        assert sidecar.read_bytes() == b"old ocr"
        allow_write.set()
        await asyncio.to_thread(write_finished.wait, 1.0)

    assert image.read_bytes() == b"old image"
    assert sidecar.read_bytes() == b"old ocr"


@pytest.mark.asyncio
async def test_uploads_for_different_threads_do_not_block_or_cross_files(tmp_path):
    uploads_a = tmp_path / "thread-a" / "uploads"
    uploads_b = tmp_path / "thread-b" / "uploads"
    uploads_a.mkdir(parents=True)
    uploads_b.mkdir(parents=True)
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    class BlockingUpload:
        filename = "same.txt"

        def __init__(self):
            self._sent = False

        async def read(self, _size):
            if self._sent:
                return b""
            self._sent = True
            first_started.set()
            await release_first.wait()
            return b"thread-a"

    def uploads_dir(thread_id, **_kwargs):
        return uploads_a if thread_id == "thread-a" else uploads_b

    with (
        patch.object(uploads, "ensure_uploads_dir", side_effect=uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=_mounted_provider()),
    ):
        first = asyncio.create_task(
            call_unwrapped(
                uploads.upload_files,
                "thread-a",
                request=MagicMock(),
                files=[BlockingUpload()],
                config=SimpleNamespace(),
            )
        )
        await first_started.wait()
        second = await asyncio.wait_for(
            call_unwrapped(
                uploads.upload_files,
                "thread-b",
                request=MagicMock(),
                files=[UploadFile(filename="same.txt", file=BytesIO(b"thread-b"))],
                config=SimpleNamespace(),
            ),
            timeout=0.5,
        )
        release_first.set()
        await first

    assert second.success is True
    assert (uploads_a / "same.txt").read_bytes() == b"thread-a"
    assert (uploads_b / "same.txt").read_bytes() == b"thread-b"


@pytest.mark.asyncio
async def test_same_thread_concurrent_uploads_are_serialized(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    class PausedUpload:
        filename = "same.txt"

        def __init__(self):
            self._sent = False

        async def read(self, _size):
            if self._sent:
                return b""
            self._sent = True
            first_started.set()
            await release_first.wait()
            return b"first-finishes-last"

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=_mounted_provider()),
    ):
        first = asyncio.create_task(
            call_unwrapped(
                uploads.upload_files,
                "thread-local",
                request=MagicMock(),
                files=[PausedUpload()],
                config=SimpleNamespace(),
            )
        )
        await first_started.wait()
        second = asyncio.create_task(
            call_unwrapped(
                uploads.upload_files,
                "thread-local",
                request=MagicMock(),
                files=[UploadFile(filename="same.txt", file=BytesIO(b"second-starts-second"))],
                config=SimpleNamespace(),
            )
        )
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(second), timeout=0.05)
        release_first.set()
        first_result = await first
        second_result = await second

    assert first_result.success is True
    assert second_result.success is True
    assert (thread_uploads_dir / "same.txt").read_bytes() == b"second-starts-second"


def test_replacing_image_without_ocr_removes_stale_sidecar(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    (thread_uploads_dir / "image.png").write_bytes(b"old image")
    stale_sidecar = thread_uploads_dir / "image.png.ocr.txt"
    stale_sidecar.write_text("old image text", encoding="utf-8")

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=_mounted_provider()),
        patch.object(uploads, "extract_image_text", return_value=None),
    ):
        result = asyncio.run(
            call_unwrapped(
                uploads.upload_files,
                "thread-local",
                request=MagicMock(),
                files=[UploadFile(filename="image.png", file=BytesIO(b"new image"))],
                config=SimpleNamespace(),
            )
        )

    assert result.success is True
    assert (thread_uploads_dir / "image.png").read_bytes() == b"new image"
    assert not stale_sidecar.exists()


def test_replacing_remote_image_without_ocr_deletes_stale_sidecar(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    (thread_uploads_dir / "image.png").write_bytes(b"old image")
    (thread_uploads_dir / "image.png.ocr.txt").write_text(
        "old image text",
        encoding="utf-8",
    )
    provider = MagicMock()
    provider.uses_thread_data_mounts = False
    provider.acquire.return_value = "remote"
    sandbox = MagicMock()
    sandbox.execute_command.return_value = "__DEERFLOW_UPLOAD_REMOVED__"
    provider.get.return_value = sandbox

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch.object(uploads, "extract_image_text", return_value=None),
    ):
        result = asyncio.run(
            call_unwrapped(
                uploads.upload_files,
                "thread-remote",
                request=_request_for_user("owner-upload"),
                files=[
                    UploadFile(
                        filename="image.png",
                        file=BytesIO(b"new image"),
                    )
                ],
                config=SimpleNamespace(),
            )
        )

    assert result.success is True
    sandbox.execute_command.assert_called_once()
    assert "/mnt/user-data/uploads/image.png.ocr.txt" in sandbox.execute_command.call_args.args[0]


def test_replacing_document_without_conversion_removes_stale_markdown(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    (thread_uploads_dir / "report.pdf").write_bytes(b"old pdf")
    stale_markdown = thread_uploads_dir / "report.md"
    stale_markdown.write_text("old conversion", encoding="utf-8")

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=_mounted_provider()),
        patch.object(uploads, "_auto_convert_documents_enabled", return_value=True),
        patch.object(uploads, "convert_file_to_markdown", AsyncMock(return_value=None)),
    ):
        result = asyncio.run(
            call_unwrapped(
                uploads.upload_files,
                "thread-local",
                request=MagicMock(),
                files=[UploadFile(filename="report.pdf", file=BytesIO(b"new pdf"))],
                config=SimpleNamespace(),
            )
        )

    assert result.success is True
    assert (thread_uploads_dir / "report.pdf").read_bytes() == b"new pdf"
    assert not stale_markdown.exists()


def test_replacing_remote_document_without_conversion_deletes_stale_markdown(
    tmp_path,
):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    (thread_uploads_dir / "report.pdf").write_bytes(b"old pdf")
    (thread_uploads_dir / "report.md").write_text(
        "old conversion",
        encoding="utf-8",
    )
    provider = MagicMock()
    provider.uses_thread_data_mounts = False
    provider.acquire.return_value = "remote"
    sandbox = MagicMock()
    sandbox.execute_command.return_value = "__DEERFLOW_UPLOAD_REMOVED__"
    provider.get.return_value = sandbox

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch.object(uploads, "_auto_convert_documents_enabled", return_value=True),
        patch.object(
            uploads,
            "convert_file_to_markdown",
            AsyncMock(return_value=None),
        ),
    ):
        result = asyncio.run(
            call_unwrapped(
                uploads.upload_files,
                "thread-remote",
                request=_request_for_user("owner-upload"),
                files=[
                    UploadFile(
                        filename="report.pdf",
                        file=BytesIO(b"new pdf"),
                    )
                ],
                config=SimpleNamespace(),
            )
        )

    assert result.success is True
    sandbox.execute_command.assert_called_once()
    assert "/mnt/user-data/uploads/report.md" in sandbox.execute_command.call_args.args[0]


def test_later_remote_sync_failure_restores_deleted_stale_companion(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    image = thread_uploads_dir / "image.png"
    sidecar = thread_uploads_dir / "image.png.ocr.txt"
    notes = thread_uploads_dir / "notes.txt"
    image.write_bytes(b"old image")
    sidecar.write_bytes(b"old image text")
    notes.write_bytes(b"old notes")
    image_virtual_path = "/mnt/user-data/uploads/image.png"
    sidecar_virtual_path = "/mnt/user-data/uploads/image.png.ocr.txt"
    notes_virtual_path = "/mnt/user-data/uploads/notes.txt"
    remote_files = {
        image_virtual_path: b"old image",
        sidecar_virtual_path: b"old image text",
        notes_virtual_path: b"old notes",
    }
    deleted_paths = []

    class Sandbox:
        def update_file(self, path, content):
            if path == notes_virtual_path and content == b"new notes":
                raise RuntimeError("later sandbox write failed")
            remote_files[path] = content

        def execute_command(self, command):
            assert sidecar_virtual_path in command
            deleted_paths.append(sidecar_virtual_path)
            remote_files.pop(sidecar_virtual_path, None)
            return "__DEERFLOW_UPLOAD_REMOVED__"

    provider = MagicMock()
    provider.uses_thread_data_mounts = False
    provider.acquire.return_value = "remote"
    provider.get.return_value = Sandbox()

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch.object(uploads, "extract_image_text", return_value=None),
        pytest.raises(HTTPException) as exc_info,
    ):
        asyncio.run(
            call_unwrapped(
                uploads.upload_files,
                "thread-remote",
                request=_request_for_user("owner-upload"),
                files=[
                    UploadFile(
                        filename="image.png",
                        file=BytesIO(b"new image"),
                    ),
                    UploadFile(
                        filename="notes.txt",
                        file=BytesIO(b"new notes"),
                    ),
                ],
                config=SimpleNamespace(),
            )
        )

    assert exc_info.value.status_code == 500
    assert deleted_paths == [sidecar_virtual_path]
    assert remote_files == {
        image_virtual_path: b"old image",
        sidecar_virtual_path: b"old image text",
        notes_virtual_path: b"old notes",
    }


def test_missing_written_file_during_remote_sync_rolls_back_instead_of_deleting(
    tmp_path,
):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    notes = thread_uploads_dir / "notes.txt"
    notes.write_bytes(b"old notes")
    provider = MagicMock()
    provider.uses_thread_data_mounts = False
    provider.acquire.return_value = "remote"
    sandbox = MagicMock()
    sandbox.execute_command.return_value = "__DEERFLOW_UPLOAD_REMOVED__"
    provider.get.return_value = sandbox

    def remove_written_file(file_path, **_kwargs):
        Path(file_path).unlink()

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch.object(
            uploads,
            "_make_file_sandbox_readable",
            side_effect=remove_written_file,
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        asyncio.run(
            call_unwrapped(
                uploads.upload_files,
                "thread-remote",
                request=_request_for_user("owner-upload"),
                files=[
                    UploadFile(
                        filename="notes.txt",
                        file=BytesIO(b"new notes"),
                    )
                ],
                config=SimpleNamespace(),
            )
        )

    assert exc_info.value.status_code == 500
    assert notes.read_bytes() == b"old notes"
    sandbox.execute_command.assert_not_called()


def test_replacing_document_with_conversion_disabled_removes_stale_markdown(
    tmp_path,
):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    stale_markdown = thread_uploads_dir / "report.md"
    stale_markdown.write_text("old conversion", encoding="utf-8")

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=_mounted_provider()),
        patch.object(uploads, "_auto_convert_documents_enabled", return_value=False),
    ):
        result = asyncio.run(
            call_unwrapped(
                uploads.upload_files,
                "thread-local",
                request=MagicMock(),
                files=[UploadFile(filename="report.pdf", file=BytesIO(b"new pdf"))],
                config=SimpleNamespace(),
            )
        )

    assert result.success is True
    assert not stale_markdown.exists()


def test_explicit_markdown_upload_wins_over_document_companion_in_same_request(
    tmp_path,
):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=_mounted_provider()),
        patch.object(uploads, "_auto_convert_documents_enabled", return_value=False),
    ):
        result = asyncio.run(
            call_unwrapped(
                uploads.upload_files,
                "thread-local",
                request=MagicMock(),
                files=[
                    UploadFile(filename="report.md", file=BytesIO(b"explicit")),
                    UploadFile(filename="report.pdf", file=BytesIO(b"pdf")),
                ],
                config=SimpleNamespace(),
            )
        )

    assert result.success is True
    assert [item.filename for item in result.files] == ["report.md", "report.pdf"]
    assert (thread_uploads_dir / "report.md").read_bytes() == b"explicit"


def test_later_explicit_markdown_is_reserved_before_document_conversion(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    convert = AsyncMock()

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=_mounted_provider()),
        patch.object(uploads, "_auto_convert_documents_enabled", return_value=True),
        patch.object(uploads, "convert_file_to_markdown", convert),
    ):
        result = asyncio.run(
            call_unwrapped(
                uploads.upload_files,
                "thread-local",
                request=MagicMock(),
                files=[
                    UploadFile(filename="report.pdf", file=BytesIO(b"pdf")),
                    UploadFile(filename="report.md", file=BytesIO(b"explicit")),
                ],
                config=SimpleNamespace(),
            )
        )

    assert result.success is True
    assert [item.filename for item in result.files] == ["report.pdf", "report.md"]
    assert result.files[0].markdown_file is None
    assert (thread_uploads_dir / "report.md").read_bytes() == b"explicit"
    convert.assert_not_awaited()


def test_ocr_sidecar_symlink_race_cannot_overwrite_outside_uploads(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("safe", encoding="utf-8")
    sidecar = thread_uploads_dir / "image.png.ocr.txt"

    def race_ocr(_snapshot_path):
        sidecar.symlink_to(outside)
        return "attacker text"

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=_mounted_provider()),
        patch.object(uploads, "extract_image_text", side_effect=race_ocr),
    ):
        result = asyncio.run(
            call_unwrapped(
                uploads.upload_files,
                "thread-local",
                request=MagicMock(),
                files=[UploadFile(filename="image.png", file=BytesIO(b"image"))],
                config=SimpleNamespace(),
            )
        )

    assert result.success is False
    assert result.files == []
    assert outside.read_text(encoding="utf-8") == "safe"
    assert not sidecar.exists()


def test_markdown_symlink_race_cannot_overwrite_outside_uploads(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("safe", encoding="utf-8")
    markdown_target = thread_uploads_dir / "report.md"

    async def race_conversion(snapshot_path):
        converted = snapshot_path.with_suffix(".md")
        converted.write_text("converted", encoding="utf-8")
        markdown_target.symlink_to(outside)
        return converted

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=_mounted_provider()),
        patch.object(uploads, "_auto_convert_documents_enabled", return_value=True),
        patch.object(uploads, "convert_file_to_markdown", side_effect=race_conversion),
    ):
        result = asyncio.run(
            call_unwrapped(
                uploads.upload_files,
                "thread-local",
                request=MagicMock(),
                files=[UploadFile(filename="report.pdf", file=BytesIO(b"pdf"))],
                config=SimpleNamespace(),
            )
        )

    assert result.success is False
    assert result.files == []
    assert outside.read_text(encoding="utf-8") == "safe"
    assert not markdown_target.exists()


def test_unsafe_skipped_file_rolls_back_size_and_remote_sync_targets(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("safe", encoding="utf-8")
    sidecar = thread_uploads_dir / "image.png.ocr.txt"
    provider = MagicMock()
    provider.uses_thread_data_mounts = False
    provider.acquire.return_value = "remote"
    sandbox = MagicMock()
    provider.get.return_value = sandbox

    def race_ocr(_snapshot_path):
        sidecar.symlink_to(outside)
        return "attacker text"

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch.object(uploads, "extract_image_text", side_effect=race_ocr),
        patch.object(
            uploads,
            "_get_upload_limits",
            return_value=uploads.UploadLimits(
                max_files=10,
                max_file_size=10,
                max_total_size=5,
            ),
        ),
    ):
        result = asyncio.run(
            call_unwrapped(
                uploads.upload_files,
                "thread-remote",
                request=_request_for_user("owner-upload"),
                files=[
                    UploadFile(filename="image.png", file=BytesIO(b"bad")),
                    UploadFile(filename="notes.txt", file=BytesIO(b"12345")),
                ],
                config=SimpleNamespace(),
            )
        )

    assert result.success is False
    assert [item.filename for item in result.files] == ["notes.txt"]
    sandbox.update_file.assert_called_once_with(
        "/mnt/user-data/uploads/notes.txt",
        b"12345",
    )
    assert outside.read_text(encoding="utf-8") == "safe"


def test_sandbox_sync_failure_restores_overwritten_host_file(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    existing = thread_uploads_dir / "notes.txt"
    existing.write_bytes(b"old upload")
    provider = MagicMock()
    provider.uses_thread_data_mounts = False
    provider.acquire.return_value = "remote"
    sandbox = MagicMock()
    sandbox.update_file.side_effect = RuntimeError("sandbox write failed")
    provider.get.return_value = sandbox

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        pytest.raises(HTTPException) as exc_info,
    ):
        asyncio.run(
            call_unwrapped(
                uploads.upload_files,
                "thread-remote",
                request=_request_for_user("owner-upload"),
                files=[UploadFile(filename="notes.txt", file=BytesIO(b"new upload"))],
                config=SimpleNamespace(),
            )
        )

    assert exc_info.value.status_code == 500
    assert existing.read_bytes() == b"old upload"


def test_second_sandbox_sync_failure_restores_prior_remote_writes(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    first = thread_uploads_dir / "first.txt"
    second = thread_uploads_dir / "second.txt"
    first.write_bytes(b"old first")
    second.write_bytes(b"old second")
    provider = MagicMock()
    provider.uses_thread_data_mounts = False
    provider.acquire.return_value = "remote"
    sandbox = MagicMock()
    sandbox.update_file.side_effect = [
        None,
        RuntimeError("second write failed"),
        None,
        None,
    ]
    provider.get.return_value = sandbox

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        pytest.raises(HTTPException) as exc_info,
    ):
        asyncio.run(
            call_unwrapped(
                uploads.upload_files,
                "thread-remote",
                request=_request_for_user("owner-upload"),
                files=[
                    UploadFile(filename="first.txt", file=BytesIO(b"new first")),
                    UploadFile(filename="second.txt", file=BytesIO(b"new second")),
                ],
                config=SimpleNamespace(),
            )
        )

    assert exc_info.value.status_code == 500
    assert first.read_bytes() == b"old first"
    assert second.read_bytes() == b"old second"
    assert sandbox.update_file.call_args_list == [
        (("/mnt/user-data/uploads/first.txt", b"new first"),),
        (("/mnt/user-data/uploads/second.txt", b"new second"),),
        (("/mnt/user-data/uploads/second.txt", b"old second"),),
        (("/mnt/user-data/uploads/first.txt", b"old first"),),
    ]


@pytest.mark.asyncio
async def test_image_ocr_does_not_block_gateway_event_loop(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    def slow_ocr(_path):
        time.sleep(0.2)
        return None

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=_mounted_provider()),
        patch.object(uploads, "extract_image_text", side_effect=slow_ocr),
    ):
        started = time.perf_counter()
        task = asyncio.create_task(
            call_unwrapped(
                uploads.upload_files,
                "thread-local",
                request=MagicMock(),
                files=[UploadFile(filename="image.png", file=BytesIO(b"png"))],
                config=SimpleNamespace(),
            )
        )
        await asyncio.sleep(0.02)
        tick_elapsed = time.perf_counter() - started
        await task

    assert tick_elapsed < 0.1


@pytest.mark.asyncio
async def test_remote_upload_acquire_and_update_do_not_block_event_loop(tmp_path):
    from deerflow.sandbox.sandbox_provider import SandboxProvider

    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    update_started = threading.Event()

    class SlowSandbox:
        def update_file(self, _path, _content):
            update_started.set()
            time.sleep(0.2)

    class SlowProvider(SandboxProvider):
        uses_thread_data_mounts = False

        def acquire(self, thread_id=None, *, user_id=None):
            time.sleep(0.2)
            return "remote"

        def get(self, sandbox_id):
            return SlowSandbox()

        def release(self, sandbox_id):
            return None

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=SlowProvider()),
    ):
        started = time.perf_counter()
        task = asyncio.create_task(
            call_unwrapped(
                uploads.upload_files,
                "thread-remote",
                request=_request_for_user("owner-upload"),
                files=[UploadFile(filename="notes.txt", file=BytesIO(b"hello"))],
                config=SimpleNamespace(),
            )
        )
        await asyncio.sleep(0.02)
        acquire_tick = time.perf_counter() - started
        await asyncio.to_thread(update_started.wait, 1.0)
        update_started_at = time.perf_counter()
        await asyncio.sleep(0.02)
        update_tick = time.perf_counter() - update_started_at
        await task

    assert acquire_tick < 0.1
    assert update_tick < 0.1


@pytest.mark.asyncio
async def test_cancelled_remote_sync_waits_and_restores_remote_content(tmp_path):
    from deerflow.sandbox.sandbox_provider import SandboxProvider

    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    existing = thread_uploads_dir / "notes.txt"
    existing.write_bytes(b"old")
    update_started = threading.Event()
    allow_update = threading.Event()
    remote_files = {"/mnt/user-data/uploads/notes.txt": b"old"}

    class SlowSandbox:
        def update_file(self, path, content):
            update_started.set()
            allow_update.wait(1.0)
            remote_files[path] = content

        def execute_command(self, _command):
            return "__DEERFLOW_UPLOAD_REMOVED__"

    class SlowProvider(SandboxProvider):
        uses_thread_data_mounts = False

        def acquire(self, thread_id=None, *, user_id=None):
            return "remote"

        def get(self, sandbox_id):
            return SlowSandbox()

        def release(self, sandbox_id):
            return None

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=SlowProvider()),
    ):
        upload_task = asyncio.create_task(
            call_unwrapped(
                uploads.upload_files,
                "thread-remote",
                request=_request_for_user("owner-upload"),
                files=[UploadFile(filename="notes.txt", file=BytesIO(b"new"))],
                config=SimpleNamespace(),
            )
        )
        await asyncio.to_thread(update_started.wait, 1.0)
        upload_task.cancel()
        allow_update.set()

        with pytest.raises(asyncio.CancelledError):
            await upload_task

    assert existing.read_bytes() == b"old"
    assert remote_files["/mnt/user-data/uploads/notes.txt"] == b"old"


def test_upload_files_does_not_sync_non_local_sandbox_when_total_size_exceeds_limit(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = False
    provider.acquire.return_value = "aio-1"
    sandbox = MagicMock()
    provider.get.return_value = sandbox

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch.object(uploads, "_get_upload_limits", return_value=uploads.UploadLimits(max_files=10, max_file_size=10, max_total_size=5)),
    ):
        files = [
            ChunkedUpload("first.txt", [b"123"]),
            ChunkedUpload("second.txt", [b"456"]),
        ]
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(call_unwrapped(uploads.upload_files, "thread-aio", request=_request_for_user("owner-upload"), files=files, config=SimpleNamespace()))

    assert exc_info.value.status_code == 413
    provider.acquire.assert_called_once_with("thread-aio", user_id="owner-upload")
    provider.get.assert_called_once_with("aio-1")
    sandbox.update_file.assert_not_called()


def test_upload_files_does_not_sync_non_local_sandbox_when_conversion_fails(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = False
    provider.acquire.return_value = "aio-1"
    sandbox = MagicMock()
    provider.get.return_value = sandbox

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch.object(uploads, "_auto_convert_documents_enabled", return_value=True),
        patch.object(uploads, "convert_file_to_markdown", AsyncMock(side_effect=RuntimeError("conversion failed"))),
    ):
        file = UploadFile(filename="report.pdf", file=BytesIO(b"pdf-bytes"))
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(call_unwrapped(uploads.upload_files, "thread-aio", request=_request_for_user("owner-upload"), files=[file], config=SimpleNamespace()))

    assert exc_info.value.status_code == 500
    provider.acquire.assert_called_once_with("thread-aio", user_id="owner-upload")
    provider.get.assert_called_once_with("aio-1")
    sandbox.update_file.assert_not_called()
    assert not (thread_uploads_dir / "report.pdf").exists()


def test_make_file_sandbox_writable_adds_write_bits_for_regular_files(tmp_path):
    file_path = tmp_path / "report.pdf"
    file_path.write_bytes(b"pdf-bytes")
    os_chmod_mode = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
    file_path.chmod(os_chmod_mode)

    uploads._make_file_sandbox_writable(file_path)

    updated_mode = stat.S_IMODE(file_path.stat().st_mode)
    assert updated_mode & stat.S_IWUSR
    assert updated_mode & stat.S_IWGRP
    assert updated_mode & stat.S_IWOTH


def test_make_file_sandbox_writable_skips_symlinks(tmp_path):
    file_path = tmp_path / "target-link.txt"
    file_path.write_text("hello", encoding="utf-8")
    symlink_stat = MagicMock(st_mode=stat.S_IFLNK)

    with (
        patch.object(uploads.os, "lstat", return_value=symlink_stat),
        patch.object(uploads.os, "chmod") as chmod,
    ):
        uploads._make_file_sandbox_writable(file_path)

    chmod.assert_not_called()


def test_make_file_sandbox_readable_adds_read_bits_for_regular_files(tmp_path):
    file_path = tmp_path / "data.csv"
    file_path.write_bytes(b"csv-data")
    # Simulate the 0o600 permissions set by open_upload_file_no_symlink
    file_path.chmod(0o600)

    uploads._make_file_sandbox_readable(file_path)

    updated_mode = stat.S_IMODE(file_path.stat().st_mode)
    assert updated_mode & stat.S_IRUSR
    assert updated_mode & stat.S_IRGRP
    assert updated_mode & stat.S_IROTH


def test_make_file_sandbox_readable_skips_symlinks(tmp_path):
    file_path = tmp_path / "target-link.txt"
    file_path.write_text("hello", encoding="utf-8")
    symlink_stat = MagicMock(st_mode=stat.S_IFLNK)

    with (
        patch.object(uploads.os, "lstat", return_value=symlink_stat),
        patch.object(uploads.os, "chmod") as chmod,
    ):
        uploads._make_file_sandbox_readable(file_path)

    chmod.assert_not_called()


def test_upload_files_adjusts_read_permissions_for_mounted_non_local_sandbox(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    # AIO sandbox with LocalContainerBackend: uses_thread_data_mounts=True
    # but needs_upload_permission_adjustment=True (default)
    provider = MagicMock()
    provider.uses_thread_data_mounts = True
    provider.needs_upload_permission_adjustment = True

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch.object(uploads, "_make_file_sandbox_readable") as make_readable,
    ):
        file = UploadFile(filename="notes.txt", file=BytesIO(b"hello uploads"))
        result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-aio", request=MagicMock(), files=[file], config=SimpleNamespace()))

    assert result.success is True
    make_readable.assert_called_once()
    called_path = make_readable.call_args[0][0]
    assert called_path.name == "notes.txt"


def test_upload_files_rejects_dotdot_and_dot_filenames(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.acquire.return_value = "local"
    sandbox = MagicMock()
    provider.get.return_value = sandbox

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        # These filenames must be rejected outright
        for bad_name in ["..", "."]:
            file = UploadFile(filename=bad_name, file=BytesIO(b"data"))
            result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-local", request=MagicMock(), files=[file], config=SimpleNamespace()))
            assert result.success is True
            assert result.files == [], f"Expected no files for unsafe filename {bad_name!r}"

        # Path-traversal prefixes are stripped to the basename and accepted safely
        file = UploadFile(filename="../etc/passwd", file=BytesIO(b"data"))
        result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-local", request=MagicMock(), files=[file], config=SimpleNamespace()))
        assert result.success is True
        assert len(result.files) == 1
        assert result.files[0].filename == "passwd"

    # Only the safely normalised file should exist
    assert [f.name for f in thread_uploads_dir.iterdir()] == ["passwd"]


def test_upload_files_rejects_preexisting_symlink_destination(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("protected", encoding="utf-8")
    (thread_uploads_dir / "victim.txt").symlink_to(outside_file)

    provider = MagicMock()
    provider.uses_thread_data_mounts = True

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        file = UploadFile(filename="victim.txt", file=BytesIO(b"attacker upload"))
        result = asyncio.run(uploads.upload_files("thread-local", files=[file]))

    assert result.success is False
    assert result.files == []
    assert result.skipped_files == ["victim.txt"]
    assert "skipped 1 unsafe file" in result.message
    assert outside_file.read_text(encoding="utf-8") == "protected"
    assert (thread_uploads_dir / "victim.txt").is_symlink()


def test_write_upload_rejects_symlinked_ancestor_directory(tmp_path):
    outside = tmp_path / "foreign-uploads"
    outside.mkdir()
    victim = outside / "victim.txt"
    victim.write_text("foreign content", encoding="utf-8")
    uploads_link = tmp_path / "uploads-link"
    uploads_link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(uploads.UnsafeUploadPathError):
        uploads.write_upload_file_no_symlink(
            uploads_link,
            "victim.txt",
            b"owner overwrite",
        )

    assert victim.read_text(encoding="utf-8") == "foreign content"


def test_upload_transaction_keeps_permission_change_on_pinned_directory(tmp_path):
    thread_uploads_dir = tmp_path / "owner-uploads"
    thread_uploads_dir.mkdir()
    detached_uploads_dir = tmp_path / "detached-owner-uploads"
    outside_dir = tmp_path / "other-owner-uploads"
    outside_dir.mkdir()
    outside_file = outside_dir / "victim.txt"
    outside_file.write_bytes(b"other owner")
    outside_file.chmod(0o600)

    class SwappingUpload:
        filename = "victim.txt"
        sent = False

        async def read(self, _size):
            if self.sent:
                return b""
            self.sent = True
            thread_uploads_dir.rename(detached_uploads_dir)
            thread_uploads_dir.symlink_to(outside_dir, target_is_directory=True)
            return b"owner upload"

    provider = MagicMock()
    provider.uses_thread_data_mounts = True
    provider.needs_upload_permission_adjustment = True

    with (
        patch.object(
            uploads,
            "ensure_uploads_dir",
            return_value=thread_uploads_dir,
        ),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        result = asyncio.run(
            call_unwrapped(
                uploads.upload_files,
                "thread-local",
                request=MagicMock(),
                files=[SwappingUpload()],
                config=SimpleNamespace(),
            )
        )

    assert result.success is True
    assert outside_file.read_bytes() == b"other owner"
    assert stat.S_IMODE(outside_file.stat().st_mode) == 0o600
    assert (detached_uploads_dir / "victim.txt").read_bytes() == b"owner upload"


def test_delete_upload_rejects_symlinked_ancestor_directory(tmp_path):
    outside = tmp_path / "foreign-uploads"
    outside.mkdir()
    victim = outside / "victim.txt"
    victim.write_text("foreign content", encoding="utf-8")
    uploads_link = tmp_path / "uploads-link"
    uploads_link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(uploads.UnsafeUploadPathError):
        uploads.delete_file_safe(uploads_link, "victim.txt")

    assert victim.read_text(encoding="utf-8") == "foreign content"


def test_upload_files_rejects_dangling_symlink_destination(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    missing_target = tmp_path / "missing-target.txt"
    (thread_uploads_dir / "victim.txt").symlink_to(missing_target)

    provider = MagicMock()
    provider.uses_thread_data_mounts = True

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        file = UploadFile(filename="victim.txt", file=BytesIO(b"attacker upload"))
        result = asyncio.run(uploads.upload_files("thread-local", files=[file]))

    assert result.success is False
    assert result.files == []
    assert result.skipped_files == ["victim.txt"]
    assert not missing_target.exists()
    assert (thread_uploads_dir / "victim.txt").is_symlink()


def test_upload_files_rejects_hardlinked_destination_without_truncating(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("protected", encoding="utf-8")
    os.link(outside_file, thread_uploads_dir / "victim.txt")

    provider = MagicMock()
    provider.uses_thread_data_mounts = True

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        file = UploadFile(filename="victim.txt", file=BytesIO(b"attacker upload"))
        result = asyncio.run(uploads.upload_files("thread-local", files=[file]))

    assert result.success is False
    assert result.files == []
    assert result.skipped_files == ["victim.txt"]
    assert outside_file.read_text(encoding="utf-8") == "protected"
    assert (thread_uploads_dir / "victim.txt").read_text(encoding="utf-8") == "protected"


def test_upload_files_overwrites_existing_regular_file(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    existing_file = thread_uploads_dir / "notes.txt"
    existing_file.write_bytes(b"old upload")
    assert existing_file.stat().st_nlink == 1

    provider = MagicMock()
    provider.uses_thread_data_mounts = True

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        file = UploadFile(filename="notes.txt", file=BytesIO(b"new upload"))
        result = asyncio.run(uploads.upload_files("thread-local", files=[file]))

    assert result.success is True
    assert [file_info.filename for file_info in result.files] == ["notes.txt"]
    assert existing_file.read_bytes() == b"new upload"
    assert existing_file.stat().st_nlink == 1


def test_delete_uploaded_file_removes_generated_markdown_companion(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    (thread_uploads_dir / "report.pdf").write_bytes(b"pdf-bytes")
    (thread_uploads_dir / "report.md").write_text("converted", encoding="utf-8")

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(
            uploads,
            "get_sandbox_provider",
            return_value=_mounted_provider(),
        ),
    ):
        result = asyncio.run(call_unwrapped(uploads.delete_uploaded_file, "thread-aio", "report.pdf", request=MagicMock()))

    assert result == {"success": True, "message": "Deleted report.pdf"}
    assert not (thread_uploads_dir / "report.pdf").exists()
    assert not (thread_uploads_dir / "report.md").exists()


@pytest.mark.asyncio
async def test_same_thread_delete_waits_for_inflight_upload(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    upload_started = asyncio.Event()
    release_upload = asyncio.Event()

    class PausedUpload:
        filename = "same.txt"

        def __init__(self):
            self._sent = False

        async def read(self, _size):
            if self._sent:
                return b""
            self._sent = True
            upload_started.set()
            await release_upload.wait()
            return b"uploaded"

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=_mounted_provider()),
    ):
        upload_task = asyncio.create_task(
            call_unwrapped(
                uploads.upload_files,
                "thread-local",
                request=MagicMock(),
                files=[PausedUpload()],
                config=SimpleNamespace(),
            )
        )
        await upload_started.wait()
        delete_task = asyncio.create_task(
            call_unwrapped(
                uploads.delete_uploaded_file,
                "thread-local",
                "same.txt",
                request=MagicMock(),
            )
        )
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(delete_task), timeout=0.05)
        release_upload.set()
        await upload_task
        delete_result = await delete_task

    assert delete_result["success"] is True
    assert not (thread_uploads_dir / "same.txt").exists()


def test_delete_uploaded_file_removes_remote_base_and_companions(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    (thread_uploads_dir / "report.pdf").write_bytes(b"pdf-bytes")
    (thread_uploads_dir / "report.md").write_text("converted", encoding="utf-8")
    (thread_uploads_dir / "report.pdf.ocr.txt").write_text(
        "ocr",
        encoding="utf-8",
    )
    provider = MagicMock()
    provider.uses_thread_data_mounts = False
    provider.acquire.return_value = "remote"
    sandbox = MagicMock()
    sandbox.execute_command.return_value = "__DEERFLOW_UPLOAD_REMOVED__"
    provider.get.return_value = sandbox

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        result = asyncio.run(
            call_unwrapped(
                uploads.delete_uploaded_file,
                "thread-remote",
                "report.pdf",
                request=_request_for_user("owner-upload"),
            )
        )

    assert result == {"success": True, "message": "Deleted report.pdf"}
    commands = [call.args[0] for call in sandbox.execute_command.call_args_list]
    assert len(commands) == 3
    assert all("rm -f --" in command for command in commands)
    assert any("report.pdf" in command for command in commands)
    assert any("report.md" in command for command in commands)
    assert any("report.pdf.ocr.txt" in command for command in commands)


def test_remote_delete_failure_restores_host_and_attempted_remote_file(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    base = thread_uploads_dir / "report.pdf"
    markdown = thread_uploads_dir / "report.md"
    base.write_bytes(b"pdf-bytes")
    markdown.write_bytes(b"converted")
    provider = MagicMock()
    provider.uses_thread_data_mounts = False
    provider.acquire.return_value = "remote"
    sandbox = MagicMock()
    sandbox.execute_command.side_effect = RuntimeError("remote delete failed")
    provider.get.return_value = sandbox

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        pytest.raises(HTTPException) as exc_info,
    ):
        asyncio.run(
            call_unwrapped(
                uploads.delete_uploaded_file,
                "thread-remote",
                "report.pdf",
                request=_request_for_user("owner-upload"),
            )
        )

    assert exc_info.value.status_code == 500
    assert base.read_bytes() == b"pdf-bytes"
    assert markdown.read_bytes() == b"converted"
    sandbox.update_file.assert_called_once_with(
        "/mnt/user-data/uploads/report.pdf",
        b"pdf-bytes",
    )
    provider.destroy.assert_not_called()


def test_remote_delete_rollback_stays_on_pinned_directory_after_ancestor_swap(
    tmp_path,
):
    thread_uploads_dir = tmp_path / "owner-uploads"
    thread_uploads_dir.mkdir()
    detached_uploads_dir = tmp_path / "detached-owner-uploads"
    base = thread_uploads_dir / "report.pdf"
    base.write_bytes(b"pdf-bytes")
    outside_dir = tmp_path / "other-owner-uploads"
    outside_dir.mkdir()

    provider = MagicMock()
    provider.uses_thread_data_mounts = False
    provider.acquire.return_value = "remote"
    sandbox = MagicMock()

    def fail_remote_delete(_command):
        if not thread_uploads_dir.is_symlink():
            thread_uploads_dir.rename(detached_uploads_dir)
            thread_uploads_dir.symlink_to(outside_dir, target_is_directory=True)
        raise RuntimeError("remote delete failed")

    sandbox.execute_command.side_effect = fail_remote_delete
    provider.get.return_value = sandbox

    with (
        patch.object(
            uploads,
            "get_uploads_dir",
            return_value=thread_uploads_dir,
        ),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        pytest.raises(HTTPException) as exc_info,
    ):
        asyncio.run(
            call_unwrapped(
                uploads.delete_uploaded_file,
                "thread-remote",
                "report.pdf",
                request=_request_for_user("owner-upload"),
            )
        )

    assert exc_info.value.status_code == 500
    assert list(outside_dir.iterdir()) == []
    assert (detached_uploads_dir / "report.pdf").read_bytes() == b"pdf-bytes"


def test_remote_delete_restore_failure_retains_local_recovery_copy(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    base = thread_uploads_dir / "report.pdf"
    base.write_bytes(b"pdf-bytes")
    provider = MagicMock()
    provider.uses_thread_data_mounts = False
    provider.acquire.return_value = "remote"
    sandbox = MagicMock()
    sandbox.execute_command.side_effect = RuntimeError("remote unavailable")
    provider.get.return_value = sandbox
    original_write = uploads.write_upload_file_no_symlink

    def fail_primary_restore(base_dir, filename, data, **kwargs):
        if Path(base_dir) == thread_uploads_dir:
            raise OSError("host restore blocked")
        return original_write(base_dir, filename, data, **kwargs)

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch.object(uploads, "write_upload_file_no_symlink", side_effect=fail_primary_restore),
        pytest.raises(HTTPException) as exc_info,
    ):
        asyncio.run(
            call_unwrapped(
                uploads.delete_uploaded_file,
                "thread-remote",
                "report.pdf",
                request=_request_for_user("owner-upload"),
            )
        )

    assert exc_info.value.status_code == 500
    recovery_files = list(thread_uploads_dir.glob(".deerflow-delete-recovery-*/*"))
    assert len(recovery_files) == 1
    assert recovery_files[0].read_bytes() == b"pdf-bytes"
    provider.destroy.assert_called_once_with("remote")


@pytest.mark.asyncio
async def test_cancelled_local_delete_finishes_remote_delete_before_exit(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    base = thread_uploads_dir / "report.pdf"
    base.write_bytes(b"pdf-bytes")
    delete_started = threading.Event()
    allow_delete = threading.Event()
    original_delete = uploads.delete_file_safe

    def slow_delete(*args, **kwargs):
        delete_started.set()
        allow_delete.wait(1.0)
        return original_delete(*args, **kwargs)

    provider = MagicMock()
    provider.uses_thread_data_mounts = False
    provider.acquire.return_value = "remote"
    sandbox = MagicMock()
    sandbox.execute_command.return_value = "__DEERFLOW_UPLOAD_REMOVED__"
    provider.get.return_value = sandbox

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch.object(uploads, "delete_file_safe", side_effect=slow_delete),
    ):
        delete_task = asyncio.create_task(
            call_unwrapped(
                uploads.delete_uploaded_file,
                "thread-remote",
                "report.pdf",
                request=_request_for_user("owner-upload"),
            )
        )
        await asyncio.to_thread(delete_started.wait, 1.0)
        delete_task.cancel()
        allow_delete.set()

        with pytest.raises(asyncio.CancelledError):
            await delete_task

    assert not base.exists()
    assert sandbox.execute_command.call_count == 3


def test_auto_convert_documents_enabled_defaults_to_false_on_config_errors():
    class BrokenConfig:
        def __getattribute__(self, name):
            if name == "uploads":
                raise RuntimeError("boom")
            return super().__getattribute__(name)

    assert uploads._auto_convert_documents_enabled(BrokenConfig()) is False


def test_auto_convert_documents_enabled_reads_dict_backed_uploads_config():
    cfg = MagicMock()
    cfg.uploads = {"auto_convert_documents": True}

    assert uploads._auto_convert_documents_enabled(cfg) is True


def test_auto_convert_documents_enabled_accepts_boolean_and_string_truthy_values():
    false_cfg = MagicMock()
    false_cfg.uploads = MagicMock(auto_convert_documents=False)

    true_cfg = MagicMock()
    true_cfg.uploads = MagicMock(auto_convert_documents=True)

    string_true_cfg = MagicMock()
    string_true_cfg.uploads = MagicMock(auto_convert_documents="YES")

    string_false_cfg = MagicMock()
    string_false_cfg.uploads = MagicMock(auto_convert_documents="false")

    assert uploads._auto_convert_documents_enabled(false_cfg) is False
    assert uploads._auto_convert_documents_enabled(true_cfg) is True
    assert uploads._auto_convert_documents_enabled(string_true_cfg) is True
    assert uploads._auto_convert_documents_enabled(string_false_cfg) is False


def test_upload_limits_endpoint_reads_uploads_config():
    cfg = MagicMock()
    cfg.uploads = {
        "max_files": 15,
        "max_file_size": "1048576",
        "max_total_size": 2097152,
    }

    result = asyncio.run(call_unwrapped(uploads.get_upload_limits, "thread-local", request=MagicMock(), config=cfg))

    assert result.max_files == 15
    assert result.max_file_size == 1048576
    assert result.max_total_size == 2097152


def test_upload_limits_endpoint_requires_thread_access():
    cfg = MagicMock()
    cfg.uploads = {}
    app = make_authed_test_app(owner_check_passes=False)
    app.state.config = cfg
    app.dependency_overrides[get_config] = lambda: cfg
    app.include_router(uploads.router)

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-local/uploads/limits")

    assert response.status_code == 404


def test_upload_limits_accept_legacy_config_keys():
    cfg = MagicMock()
    cfg.uploads = {
        "max_file_count": 7,
        "max_single_file_size": 123,
        "max_total_size": 456,
    }

    limits = uploads._get_upload_limits(cfg)

    assert limits == uploads.UploadLimits(max_files=7, max_file_size=123, max_total_size=456)


def test_upload_files_uses_configured_file_count_limit(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    cfg = MagicMock()
    cfg.uploads = {"max_files": 1}

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=_mounted_provider()),
    ):
        files = [
            ChunkedUpload("one.txt", [b"one"]),
            ChunkedUpload("two.txt", [b"two"]),
        ]
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(call_unwrapped(uploads.upload_files, "thread-local", request=MagicMock(), files=files, config=cfg))

    assert exc_info.value.status_code == 413
