from __future__ import annotations

import asyncio
import importlib
import os
import shutil
import threading
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _router_auth_helpers import call_unwrapped
from fastapi import HTTPException, UploadFile
from langgraph.runtime import Runtime

from app.gateway.routers import uploads
from deerflow.client import DeerFlowClient
from deerflow.community.aio_sandbox.sandbox_info import SandboxInfo
from deerflow.sandbox.middleware import SandboxMiddleware
from deerflow.uploads.manager import (
    UnsafeUploadPathError,
    _replace_upload_atomically,
    acquire_upload_transaction_lock,
    claim_unique_filename,
    copy_upload_file_no_symlink,
    normalize_filename,
    release_upload_transaction_lock,
)


def _mounted_provider() -> MagicMock:
    provider = MagicMock()
    provider.uses_thread_data_mounts = True
    provider.needs_upload_permission_adjustment = False
    return provider


def test_gateway_conversion_does_not_overwrite_uploaded_markdown(tmp_path: Path) -> None:
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()

    async def fake_convert(file_path: Path, *, output_path: Path | None = None) -> Path:
        target = output_path or file_path.with_suffix(".md")
        target.write_text("converted pdf", encoding="utf-8")
        return target

    paths = SimpleNamespace(sandbox_uploads_dir=lambda *_args, **_kwargs: uploads_dir)
    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=uploads_dir),
        patch.object(uploads, "get_request_storage_user_id", return_value="user-1"),
        patch.object(uploads, "get_paths", return_value=paths),
        patch.object(uploads, "get_sandbox_provider", return_value=_mounted_provider()),
        patch.object(uploads, "_auto_convert_documents_enabled", return_value=True),
        patch.object(uploads, "extract_image_text", return_value=None),
        patch.object(uploads, "convert_file_to_markdown", side_effect=fake_convert),
    ):
        result = asyncio.run(
            call_unwrapped(
                uploads.upload_files,
                "thread-1",
                request=MagicMock(),
                files=[
                    UploadFile(filename="report.md", file=BytesIO(b"original markdown")),
                    UploadFile(filename="report.pdf", file=BytesIO(b"pdf")),
                ],
                config=SimpleNamespace(uploads={}),
            )
        )

    assert (uploads_dir / "report.md").read_text(encoding="utf-8") == "original markdown"
    assert (uploads_dir / "report_1.md").read_text(encoding="utf-8") == "converted pdf"
    assert result.files[1].markdown_file == "report_1.md"


def test_gateway_rejects_reserved_ocr_sidecar_filename(tmp_path: Path) -> None:
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    paths = SimpleNamespace(sandbox_uploads_dir=lambda *_args, **_kwargs: uploads_dir)
    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=uploads_dir),
        patch.object(uploads, "get_request_storage_user_id", return_value="user-1"),
        patch.object(uploads, "get_paths", return_value=paths),
        patch.object(uploads, "get_sandbox_provider", return_value=_mounted_provider()),
    ):
        result = asyncio.run(
            call_unwrapped(
                uploads.upload_files,
                "thread-1",
                request=MagicMock(),
                files=[
                    UploadFile(
                        filename="photo.png.ocr.txt",
                        file=BytesIO(b"user data"),
                    ),
                    UploadFile(filename="", file=BytesIO(b"unnamed")),
                ],
                config=SimpleNamespace(uploads={}),
            )
        )

    assert result.success is False
    assert result.files == []
    assert result.skipped_files == ["photo.png.ocr.txt", "<unnamed>"]
    assert not (uploads_dir / "photo.png.ocr.txt").exists()


def test_gateway_upload_offloads_backup_rollback_and_discard(tmp_path: Path) -> None:
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    existing = uploads_dir / "report.md"
    existing.write_text("original markdown", encoding="utf-8")
    paths = SimpleNamespace(sandbox_uploads_dir=lambda *_args, **_kwargs: uploads_dir)
    real_to_thread = asyncio.to_thread
    offloaded: list[object] = []

    async def recording_to_thread(func, *args, **kwargs):
        offloaded.append(func)
        return await real_to_thread(func, *args, **kwargs)

    async def fail_write(*_args, **_kwargs):
        raise HTTPException(status_code=413, detail="forced failure")

    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=uploads_dir),
        patch.object(uploads, "get_request_storage_user_id", return_value="user-1"),
        patch.object(uploads, "get_paths", return_value=paths),
        patch.object(uploads, "get_sandbox_provider", return_value=_mounted_provider()),
        patch.object(uploads, "_write_upload_file_with_limits", side_effect=fail_write),
        patch.object(uploads.asyncio, "to_thread", side_effect=recording_to_thread),
        pytest.raises(HTTPException, match="forced failure"),
    ):
        asyncio.run(
            call_unwrapped(
                uploads.upload_files,
                "thread-1",
                request=MagicMock(),
                files=[UploadFile(filename="report.md", file=BytesIO(b"replacement"))],
                config=SimpleNamespace(uploads={}),
            )
        )

    assert existing.read_text(encoding="utf-8") == "original markdown"
    assert not list(tmp_path.rglob(".deerflow-upload-backup-*"))
    assert uploads._backup_existing_upload in offloaded
    assert uploads._restore_upload_backups in offloaded
    assert uploads._discard_upload_backups in offloaded


def test_failed_upload_restore_keeps_the_only_backup(tmp_path: Path) -> None:
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    target = uploads_dir / "report.md"
    target.write_text("partial replacement", encoding="utf-8")
    backup = backup_dir / "0"
    backup.write_text("original", encoding="utf-8")
    backups = {target: backup}

    uploads_fd = os.open(uploads_dir, os.O_RDONLY)
    backup_fd = os.open(backup_dir, os.O_RDONLY)
    try:
        with patch.object(
            uploads.os,
            "replace",
            side_effect=OSError("disk error"),
        ):
            uploads._restore_upload_backups(
                backups,
                uploads_fd=uploads_fd,
                backup_fd=backup_fd,
            )
    finally:
        os.close(uploads_fd)
        os.close(backup_fd)

    assert backups == {target: backup}
    assert backup.read_text(encoding="utf-8") == "original"
    assert target.read_text(encoding="utf-8") == "partial replacement"
    assert (backup_dir / uploads._RESTORE_FAILURE_MARKER).is_file()
    assert backup_dir.exists()


def test_embedded_client_conversion_does_not_overwrite_uploaded_markdown(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    markdown_source = source_dir / "report.md"
    markdown_source.write_text("original markdown", encoding="utf-8")
    pdf_source = source_dir / "report.pdf"
    pdf_source.write_bytes(b"pdf")
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()

    async def fake_convert(file_path: Path, *, output_path: Path | None = None) -> Path:
        target = output_path or file_path.with_suffix(".md")
        target.write_text("converted pdf", encoding="utf-8")
        return target

    client_module = importlib.import_module("deerflow.client")
    conversion_module = importlib.import_module("deerflow.utils.file_conversion")
    client = DeerFlowClient.__new__(DeerFlowClient)
    with (
        patch.object(client_module, "ensure_uploads_dir", return_value=uploads_dir),
        patch.object(conversion_module, "CONVERTIBLE_EXTENSIONS", {".pdf"}),
        patch.object(conversion_module, "convert_file_to_markdown", side_effect=fake_convert),
    ):
        result = client.upload_files("thread-1", [markdown_source, pdf_source])

    assert (uploads_dir / "report.md").read_text(encoding="utf-8") == "original markdown"
    assert (uploads_dir / "report_1.md").read_text(encoding="utf-8") == "converted pdf"
    assert result["files"][1]["markdown_file"] == "report_1.md"


def test_aio_sandbox_release_waits_for_final_lease() -> None:
    aio_module = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = aio_module.AioSandboxProvider.__new__(aio_module.AioSandboxProvider)
    sandbox_id = "sandbox-1"
    sandbox = MagicMock()
    info = SandboxInfo(
        sandbox_id=sandbox_id,
        sandbox_url="http://sandbox",
        sandbox_api_key="scoped-key",
        status="Running",
        ready=True,
    )
    provider._lock = threading.Lock()
    provider._thread_locks = {}
    provider._sandboxes = {sandbox_id: sandbox}
    provider._sandbox_infos = {sandbox_id: info}
    provider._thread_sandboxes = {("user-1", "thread-1"): sandbox_id}
    provider._last_activity = {sandbox_id: 0.0}
    provider._warm_pool = {}
    provider._backend = MagicMock()
    provider._backend.is_alive.return_value = True

    assert provider.acquire("thread-1", user_id="user-1") == sandbox_id
    assert provider.acquire("thread-1", user_id="user-1") == sandbox_id

    provider.release(sandbox_id)

    assert provider.get(sandbox_id) is sandbox
    sandbox.close.assert_not_called()

    provider.release(sandbox_id)

    sandbox.close.assert_called_once_with()
    assert provider.get(sandbox_id) is None
    assert provider._warm_pool[sandbox_id][0] is info
    assert provider._lease_counts.get(sandbox_id, 0) == 0


def test_lazy_middleware_retains_persisted_sandbox_before_release() -> None:
    middleware_module = importlib.import_module("deerflow.sandbox.middleware")
    provider = MagicMock()
    provider.retain = MagicMock(return_value=True)
    state = {"sandbox": {"sandbox_id": "sandbox-1"}}
    runtime = Runtime(context={"thread_id": "thread-1", "user_id": "user-1"})
    middleware = SandboxMiddleware(lazy_init=True)

    with patch.object(middleware_module, "get_sandbox_provider", return_value=provider):
        result = asyncio.run(middleware.abefore_agent(state, runtime))
        asyncio.run(middleware.aafter_agent(state, runtime))

    assert result is None
    provider.retain.assert_called_once_with("sandbox-1")
    provider.release.assert_called_once_with("sandbox-1")


def test_gateway_remote_sandbox_lease_is_released_after_upload(tmp_path: Path) -> None:
    from deerflow.sandbox.sandbox_provider import SandboxProvider

    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    sandbox = MagicMock()

    class RemoteProvider(SandboxProvider):
        uses_thread_data_mounts = False
        needs_upload_permission_adjustment = False

        def __init__(self) -> None:
            self.released: list[str] = []

        def acquire(self, thread_id=None, *, user_id=None):
            return "remote-1"

        def get(self, sandbox_id):
            return sandbox

        def release(self, sandbox_id):
            self.released.append(sandbox_id)

    provider = RemoteProvider()
    paths = SimpleNamespace(sandbox_uploads_dir=lambda *_args, **_kwargs: uploads_dir)
    with (
        patch.object(uploads, "ensure_uploads_dir", return_value=uploads_dir),
        patch.object(uploads, "get_request_storage_user_id", return_value="user-1"),
        patch.object(uploads, "get_paths", return_value=paths),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch.object(uploads, "extract_image_text", return_value=None),
    ):
        result = asyncio.run(
            call_unwrapped(
                uploads.upload_files,
                "thread-1",
                request=MagicMock(),
                files=[UploadFile(filename="report.txt", file=BytesIO(b"report"))],
                config=SimpleNamespace(uploads={}),
            )
        )

    assert result.success is True
    assert provider.released == ["remote-1"]


def test_feishu_sandbox_lease_is_released_when_sync_fails(tmp_path: Path) -> None:
    from app.channels.feishu import FeishuChannel
    from app.channels.message_bus import MessageBus
    from deerflow.config.paths import Paths

    channel = FeishuChannel(MessageBus(), {"app_id": "test", "app_secret": "test"})
    channel._GetMessageResourceRequest = MagicMock()
    builder = MagicMock()
    builder.message_id.return_value = builder
    builder.file_key.return_value = builder
    builder.type.return_value = builder
    builder.build.return_value = object()
    channel._GetMessageResourceRequest.builder.return_value = builder

    response = MagicMock()
    response.success.return_value = True
    response.file = BytesIO(b"file-bytes")
    response.file_name = "report.md"
    channel._api_client = MagicMock()
    channel._api_client.im.v1.message_resource.get.return_value = response

    provider = MagicMock()
    provider.acquire_async = AsyncMock(return_value="remote-1")
    sandbox = MagicMock()
    sandbox.update_file.side_effect = RuntimeError("sync failed")
    provider.get.return_value = sandbox

    with (
        patch("app.channels.feishu.get_paths", return_value=Paths(base_dir=tmp_path)),
        patch("app.channels.feishu.get_sandbox_provider", return_value=provider),
    ):
        result = asyncio.run(
            channel._receive_single_file(
                "message-1",
                "file-key",
                "file",
                "thread-1",
                user_id="user-1",
            )
        )

    assert result == "Failed to obtain the [file]"
    provider.acquire_async.assert_awaited_once_with("thread-1", user_id="user-1")
    provider.release.assert_called_once_with("remote-1")


def test_list_uploaded_files_offloads_complete_filesystem_operation(tmp_path: Path) -> None:
    list_result = {"files": [], "count": 0}
    to_thread = AsyncMock(side_effect=lambda func, *args, **kwargs: func(*args, **kwargs))
    paths = SimpleNamespace(sandbox_uploads_dir=lambda *_args, **_kwargs: tmp_path)

    with (
        patch.object(uploads, "get_uploads_dir", return_value=tmp_path),
        patch.object(uploads, "get_request_storage_user_id", return_value="user-1"),
        patch.object(uploads, "get_paths", return_value=paths),
        patch.object(
            uploads,
            "_list_and_enrich_uploaded_files",
            return_value=list_result,
        ) as list_files,
        patch.object(uploads.asyncio, "to_thread", to_thread),
    ):
        result = asyncio.run(call_unwrapped(uploads.list_uploaded_files, "thread-1", request=MagicMock()))

    assert result.count == 0
    to_thread.assert_awaited_once_with(list_files, tmp_path, "thread-1")
    list_files.assert_called_once_with(tmp_path, "thread-1")


def test_delete_uploaded_file_offloads_complete_filesystem_operation(tmp_path: Path) -> None:
    delete_result = {"success": True, "message": "deleted"}
    to_thread = AsyncMock(side_effect=lambda func, *args, **kwargs: func(*args, **kwargs))

    with (
        patch.object(uploads, "get_uploads_dir", return_value=tmp_path),
        patch.object(uploads, "get_request_storage_user_id", return_value="user-1"),
        patch.object(uploads, "delete_file_safe", return_value=delete_result) as delete_file,
        patch.object(uploads.asyncio, "to_thread", to_thread),
    ):
        result = asyncio.run(
            call_unwrapped(
                uploads.delete_uploaded_file,
                "thread-1",
                "report.pdf",
                request=MagicMock(),
            )
        )

    assert result == delete_result
    delete_file.assert_called_once()
    args, kwargs = delete_file.call_args
    assert args == (tmp_path, "report.pdf")
    assert isinstance(kwargs["directory_fd"], int)


def test_duplicate_max_length_utf8_filename_stays_within_filesystem_limit() -> None:
    filename = f"{'界' * 83}ab.txt"
    assert len(filename.encode("utf-8")) == 255
    seen: set[str] = set()

    assert claim_unique_filename(filename, seen) == filename
    duplicate = claim_unique_filename(filename, seen)

    assert duplicate != filename
    assert duplicate.endswith("_1.txt")
    assert len(duplicate.encode("utf-8")) <= 255
    assert normalize_filename(duplicate) == duplicate


def test_copy_upload_does_not_follow_destination_symlink(tmp_path: Path) -> None:
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    source = tmp_path / "source.txt"
    source.write_text("new content", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me", encoding="utf-8")
    (uploads_dir / "report.txt").symlink_to(outside)

    with pytest.raises(UnsafeUploadPathError):
        copy_upload_file_no_symlink(uploads_dir, "report.txt", source)

    assert outside.read_text(encoding="utf-8") == "keep me"


def test_copy_upload_rejects_exact_same_source_and_destination(
    tmp_path: Path,
) -> None:
    source = tmp_path / "same.txt"
    source.write_text("keep me", encoding="utf-8")

    with pytest.raises(shutil.SameFileError):
        copy_upload_file_no_symlink(tmp_path, "same.txt", source)

    assert source.read_text(encoding="utf-8") == "keep me"


def test_copy_upload_missing_source_preserves_existing_destination(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "report.txt"
    destination.write_text("original", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        copy_upload_file_no_symlink(
            tmp_path,
            "report.txt",
            tmp_path / "missing.txt",
        )

    assert destination.read_text(encoding="utf-8") == "original"


def test_atomic_upload_write_failure_preserves_existing_destination(
    tmp_path: Path,
) -> None:
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    destination = uploads_dir / "report.txt"
    destination.write_text("original", encoding="utf-8")

    def fail_after_partial_write(output) -> None:
        output.write(b"partial")
        raise OSError("disk error")

    with pytest.raises(OSError, match="disk error"):
        _replace_upload_atomically(
            uploads_dir,
            "report.txt",
            fail_after_partial_write,
        )

    assert destination.read_text(encoding="utf-8") == "original"
    assert not list(tmp_path.rglob(".deerflow-upload-stage-*"))


def test_atomic_upload_dirfd_resists_staging_path_swap(tmp_path: Path) -> None:
    if not all(function in uploads.os.supports_dir_fd for function in (uploads.os.open, uploads.os.rename, uploads.os.unlink)):
        pytest.skip("dir_fd-safe rename is unavailable")

    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    def swap_visible_staging_path(output) -> None:
        output.write(b"safe")
        stage = next(tmp_path.rglob(".deerflow-upload-stage-*"))
        moved = stage.with_name(f"{stage.name}-moved")
        stage.rename(moved)
        stage.mkdir()
        (stage / "payload").symlink_to(outside)

    result = _replace_upload_atomically(
        uploads_dir,
        "report.txt",
        swap_visible_staging_path,
    )

    assert result.is_symlink() is False
    assert result.read_text(encoding="utf-8") == "safe"
    assert outside.read_text(encoding="utf-8") == "outside"


def test_atomic_upload_has_path_fallback_without_dirfd_support(
    tmp_path: Path,
) -> None:
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()

    with patch.object(uploads.os, "supports_dir_fd", set()):
        result = _replace_upload_atomically(
            uploads_dir,
            "report.txt",
            lambda output: output.write(b"fallback"),
        )

    assert result.read_bytes() == b"fallback"


def test_upload_transaction_lock_serializes_same_process_threads(
    tmp_path: Path,
) -> None:
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    first = acquire_upload_transaction_lock(uploads_dir)
    second_acquired = threading.Event()
    second_handle: list[object] = []

    def acquire_second() -> None:
        second_handle.append(acquire_upload_transaction_lock(uploads_dir))
        second_acquired.set()

    thread = threading.Thread(target=acquire_second)
    thread.start()
    try:
        assert second_acquired.wait(0.1) is False
    finally:
        release_upload_transaction_lock(first)

    assert second_acquired.wait(2)
    release_upload_transaction_lock(second_handle[0])
    thread.join(timeout=2)
    assert thread.is_alive() is False
