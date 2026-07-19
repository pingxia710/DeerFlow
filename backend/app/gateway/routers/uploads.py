"""Upload router for handling file uploads."""

import asyncio
import logging
import os
import secrets
import shlex
import shutil
import stat
import tempfile
from pathlib import Path
from weakref import WeakValueDictionary

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.gateway.authz import require_permission
from app.gateway.deps import get_config
from app.gateway.path_utils import get_request_storage_user_id
from deerflow.config.app_config import AppConfig
from deerflow.config.paths import (
    UnsafePathError,
    get_paths,
    open_directory_no_symlinks,
    open_file_no_symlinks,
)
from deerflow.sandbox.sandbox_provider import SandboxProvider, get_sandbox_provider
from deerflow.uploads.manager import (
    PathTraversalError,
    UnsafeUploadPathError,
    acquire_upload_transaction_lock,
    claim_unique_filename,
    delete_file_safe,
    enrich_file_listing,
    ensure_uploads_dir,
    get_uploads_dir,
    list_files_in_dir,
    normalize_filename,
    open_upload_file_no_symlink,
    release_upload_transaction_lock,
    upload_artifact_url,
    upload_virtual_path,
    write_upload_file_no_symlink,
)
from deerflow.utils.cancellation import await_task_through_repeated_cancellation
from deerflow.utils.file_conversion import CONVERTIBLE_EXTENSIONS, convert_file_to_markdown
from deerflow.utils.image_ocr import (
    extract_image_text,
    is_supported_image_path,
    ocr_sidecar_path,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/threads/{thread_id}/uploads", tags=["uploads"])

UPLOAD_CHUNK_SIZE = 8192
DEFAULT_MAX_FILES = 10
DEFAULT_MAX_FILE_SIZE = 50 * 1024 * 1024
DEFAULT_MAX_TOTAL_SIZE = 100 * 1024 * 1024
_RESTORE_FAILURE_MARKER = ".restore-failed"
_UPLOAD_MUTATION_LOCKS: WeakValueDictionary[tuple[str, str], asyncio.Lock] = WeakValueDictionary()


async def _acquire_upload_file_lock(uploads_dir: Path):
    task = asyncio.create_task(asyncio.to_thread(acquire_upload_transaction_lock, uploads_dir))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        handle = await task
        await asyncio.to_thread(release_upload_transaction_lock, handle)
        raise


async def _release_upload_file_lock(handle) -> None:
    task = asyncio.create_task(asyncio.to_thread(release_upload_transaction_lock, handle))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class UploadedFileInfo(BaseModel):
    """Uploaded file metadata exposed by upload and list APIs."""

    filename: str
    size: int
    path: str
    virtual_path: str
    artifact_url: str
    extension: str | None = None
    modified: float | None = None
    original_filename: str | None = None
    markdown_file: str | None = None
    markdown_path: str | None = None
    markdown_virtual_path: str | None = None
    markdown_artifact_url: str | None = None
    ocr_file: str | None = None
    ocr_path: str | None = None
    ocr_virtual_path: str | None = None
    ocr_artifact_url: str | None = None


class UploadResponse(BaseModel):
    """Response model for file upload."""

    success: bool
    files: list[UploadedFileInfo]
    message: str
    skipped_files: list[str] = Field(default_factory=list)


class UploadListResponse(BaseModel):
    """Response model for uploaded file listing."""

    files: list[UploadedFileInfo]
    count: int


class UploadLimits(BaseModel):
    """Application-level upload limits exposed to clients."""

    max_files: int
    max_file_size: int
    max_total_size: int


def _open_regular_upload_file(
    file_path: os.PathLike[str] | str,
    *,
    directory_fd: int | None = None,
) -> tuple[int, os.stat_result]:
    path = Path(file_path)
    flags = os.O_RDONLY | (os.O_NONBLOCK if hasattr(os, "O_NONBLOCK") else 0)
    try:
        if directory_fd is None:
            fd = open_file_no_symlinks(path, flags)
        else:
            fd = os.open(
                path.name,
                flags | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0),
                dir_fd=directory_fd,
            )
    except FileNotFoundError:
        raise
    except (OSError, UnsafePathError) as exc:
        raise UnsafeUploadPathError(f"Unsafe upload file: {path.name}") from exc

    file_stat = os.fstat(fd)
    if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
        os.close(fd)
        raise UnsafeUploadPathError(f"Upload source is not an exclusive regular file: {path.name}")
    return fd, file_stat


def _make_file_sandbox_writable(
    file_path: os.PathLike[str] | str,
    *,
    directory_fd: int | None = None,
) -> None:
    """Ensure uploaded files remain writable when mounted into non-local sandboxes.

    In AIO sandbox mode, the gateway writes the authoritative host-side file
    first, then the sandbox runtime may rewrite the same mounted path. Granting
    world-writable access here prevents permission mismatches between the
    gateway user and the sandbox runtime user.
    """
    if directory_fd is not None:
        fd, file_stat = _open_regular_upload_file(
            file_path,
            directory_fd=directory_fd,
        )
        try:
            writable_mode = stat.S_IMODE(file_stat.st_mode) | stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH | stat.S_IRGRP | stat.S_IROTH
            os.fchmod(fd, writable_mode)
        finally:
            os.close(fd)
        return

    file_stat = os.lstat(file_path)
    if stat.S_ISLNK(file_stat.st_mode):
        logger.warning("Skipping sandbox chmod for symlinked upload path: %s", file_path)
        return

    writable_mode = stat.S_IMODE(file_stat.st_mode) | stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH | stat.S_IRGRP | stat.S_IROTH
    chmod_kwargs = {"follow_symlinks": False} if os.chmod in os.supports_follow_symlinks else {}
    os.chmod(file_path, writable_mode, **chmod_kwargs)


def _make_file_sandbox_readable(
    file_path: os.PathLike[str] | str,
    *,
    directory_fd: int | None = None,
) -> None:
    """Ensure uploaded files are readable by the sandbox process.

    For Docker sandboxes (AIO), the gateway writes files as root with 0o600
    permissions, then bind-mounts the host directory into the container. The
    sandbox process inside the container runs as a non-root user and cannot
    read those files without group/other read bits. This function adds
    ``S_IRGRP | S_IROTH`` so the sandbox can read the uploaded content.
    """
    if directory_fd is not None:
        fd, file_stat = _open_regular_upload_file(
            file_path,
            directory_fd=directory_fd,
        )
        try:
            readable_mode = stat.S_IMODE(file_stat.st_mode) | stat.S_IRGRP | stat.S_IROTH
            os.fchmod(fd, readable_mode)
        finally:
            os.close(fd)
        return

    file_stat = os.lstat(file_path)
    if stat.S_ISLNK(file_stat.st_mode):
        logger.warning("Skipping sandbox chmod for symlinked upload path: %s", file_path)
        return

    readable_mode = stat.S_IMODE(file_stat.st_mode) | stat.S_IRGRP | stat.S_IROTH
    chmod_kwargs = {"follow_symlinks": False} if os.chmod in os.supports_follow_symlinks else {}
    os.chmod(file_path, readable_mode, **chmod_kwargs)


def _uses_thread_data_mounts(sandbox_provider: SandboxProvider) -> bool:
    return bool(getattr(sandbox_provider, "uses_thread_data_mounts", False))


def _needs_upload_permission_adjustment(sandbox_provider: SandboxProvider) -> bool:
    return bool(getattr(sandbox_provider, "needs_upload_permission_adjustment", True))


def _get_uploads_config_value(app_config: AppConfig, key: str, default: object) -> object:
    """Read a value from the uploads config, supporting dict and attribute access."""
    uploads_cfg = getattr(app_config, "uploads", None)
    if isinstance(uploads_cfg, dict):
        return uploads_cfg.get(key, default)
    return getattr(uploads_cfg, key, default)


def _get_upload_limit(app_config: AppConfig, key: str, default: int, *, legacy_key: str | None = None) -> int:
    try:
        value = _get_uploads_config_value(app_config, key, None)
        if value is None and legacy_key is not None:
            value = _get_uploads_config_value(app_config, legacy_key, None)
        if value is None:
            value = default
        limit = int(value)
        if limit <= 0:
            raise ValueError
        return limit
    except Exception:
        logger.warning("Invalid uploads.%s value; falling back to %d", key, default)
        return default


def _get_upload_limits(app_config: AppConfig) -> UploadLimits:
    return UploadLimits(
        max_files=_get_upload_limit(app_config, "max_files", DEFAULT_MAX_FILES, legacy_key="max_file_count"),
        max_file_size=_get_upload_limit(app_config, "max_file_size", DEFAULT_MAX_FILE_SIZE, legacy_key="max_single_file_size"),
        max_total_size=_get_upload_limit(app_config, "max_total_size", DEFAULT_MAX_TOTAL_SIZE),
    )


def _upload_mutation_lock(thread_id: str, user_id: str) -> asyncio.Lock:
    key = (user_id, thread_id)
    lock = _UPLOAD_MUTATION_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _UPLOAD_MUTATION_LOCKS[key] = lock
    return lock


def _create_pinned_upload_subdirectory(
    uploads_dir: Path,
    uploads_fd: int,
    *,
    prefix: str,
) -> tuple[Path, int]:
    """Create and open a private child of the already-pinned uploads inode."""
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    while True:
        name = f"{prefix}{secrets.token_hex(8)}"
        try:
            os.mkdir(name, 0o700, dir_fd=uploads_fd)
        except FileExistsError:
            continue
        try:
            child_fd = os.open(name, flags, dir_fd=uploads_fd)
        except BaseException:
            shutil.rmtree(name, dir_fd=uploads_fd, ignore_errors=True)
            raise
        os.fchmod(child_fd, 0o700)
        return uploads_dir / name, child_fd


def _cleanup_uploaded_paths(
    paths: list[os.PathLike[str] | str],
    *,
    directory_fd: int | None = None,
) -> None:
    for path in reversed(paths):
        try:
            if directory_fd is None:
                os.unlink(path)
            else:
                os.unlink(Path(path).name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        except Exception:
            logger.warning("Failed to clean up upload path after rejected request: %s", path, exc_info=True)


def _backup_existing_upload(
    target: Path,
    backup_dir: Path,
    backups: dict[Path, Path],
    *,
    uploads_fd: int,
    backup_fd: int,
) -> None:
    """Move an existing regular target aside before an overwrite."""
    if target in backups:
        return
    try:
        target_stat = os.stat(
            target.name,
            dir_fd=uploads_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return
    if not stat.S_ISREG(target_stat.st_mode) or target_stat.st_nlink != 1:
        raise UnsafeUploadPathError(f"Upload destination is not an exclusive regular file: {target.name}")
    backup = backup_dir / str(len(backups))
    os.replace(
        target.name,
        backup.name,
        src_dir_fd=uploads_fd,
        dst_dir_fd=backup_fd,
    )
    backups[target] = backup


def _restore_upload_backups(
    backups: dict[Path, Path],
    targets: set[Path] | None = None,
    *,
    uploads_fd: int,
    backup_fd: int,
) -> None:
    restore_targets = list(targets if targets is not None else backups)
    restore_failed = False
    for target in reversed(restore_targets):
        backup = backups.get(target)
        if backup is None:
            continue
        try:
            os.replace(
                backup.name,
                target.name,
                src_dir_fd=backup_fd,
                dst_dir_fd=uploads_fd,
            )
        except Exception:
            restore_failed = True
            logger.exception("Failed to restore upload backup for %s", target)
        else:
            backups.pop(target, None)
    if restore_failed:
        marker_fd = os.open(
            _RESTORE_FAILURE_MARKER,
            os.O_WRONLY | os.O_CREAT,
            0o600,
            dir_fd=backup_fd,
        )
        os.close(marker_fd)
    elif not backups:
        try:
            os.unlink(_RESTORE_FAILURE_MARKER, dir_fd=backup_fd)
        except FileNotFoundError:
            pass


def _discard_upload_backups(
    backup_dir: Path,
    *,
    uploads_fd: int,
    backup_fd: int,
) -> None:
    try:
        try:
            os.stat(
                _RESTORE_FAILURE_MARKER,
                dir_fd=backup_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            retain = False
        else:
            retain = True
    finally:
        os.close(backup_fd)
    if retain:
        logger.error("Retaining failed upload rollback data in %s", backup_dir)
        return
    shutil.rmtree(
        backup_dir.name,
        dir_fd=uploads_fd,
        ignore_errors=True,
    )


def _persist_failed_delete_recovery(
    base_dir: Path,
    snapshots: dict[Path, bytes],
    *,
    directory_fd: int,
) -> Path:
    """Keep the last local copy when normal delete compensation cannot restore it."""
    recovery_dir, recovery_fd = _create_pinned_upload_subdirectory(
        base_dir,
        directory_fd,
        prefix=".deerflow-delete-recovery-",
    )
    try:
        for index, (target, content) in enumerate(snapshots.items()):
            write_upload_file_no_symlink(
                recovery_dir,
                f"{index}-{target.name}",
                content,
                directory_fd=recovery_fd,
            )
    finally:
        os.close(recovery_fd)
    return recovery_dir


def _read_regular_file_no_symlink(
    path: Path,
    *,
    directory_fd: int | None = None,
) -> bytes:
    fd, _ = _open_regular_upload_file(
        path,
        directory_fd=directory_fd,
    )
    try:
        with os.fdopen(fd, "rb") as fh:
            fd = -1
            return fh.read()
    finally:
        if fd >= 0:
            os.close(fd)


async def _run_blocking_completion_safe(func, *args, **kwargs):
    """Finish a started blocking call before propagating cancellation."""
    task = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        try:
            await await_task_through_repeated_cancellation(task)
        except Exception:
            logger.warning(
                "Blocking upload operation failed after request cancellation",
                exc_info=True,
            )
        raise cancelled


async def _run_blocking_with_deferred_cancellation(func, *args, **kwargs):
    """Return a completed blocking result together with any pending cancellation."""
    task = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
    try:
        return await asyncio.shield(task), None
    except asyncio.CancelledError as cancelled:
        result = await await_task_through_repeated_cancellation(task)
        return result, cancelled


def _remove_sandbox_file(sandbox, virtual_path: str) -> None:
    marker = "__DEERFLOW_UPLOAD_REMOVED__"
    output = sandbox.execute_command(f"rm -f -- {shlex.quote(virtual_path)} && printf {shlex.quote(marker)}")
    if marker not in str(output):
        raise RuntimeError(f"Sandbox did not confirm removal of {virtual_path}")


async def _restore_remote_sync_targets(
    sandbox,
    targets: list[tuple[Path, str]],
    *,
    directory_fd: int,
) -> None:
    restored: set[str] = set()
    for file_path, virtual_path in reversed(targets):
        if virtual_path in restored:
            continue
        restored.add(virtual_path)
        try:
            content = await asyncio.to_thread(
                _read_regular_file_no_symlink,
                file_path,
                directory_fd=directory_fd,
            )
        except FileNotFoundError:
            await _run_blocking_completion_safe(
                _remove_sandbox_file,
                sandbox,
                virtual_path,
            )
        else:
            await _run_blocking_completion_safe(
                sandbox.update_file,
                virtual_path,
                content,
            )


async def _discard_inconsistent_sandbox(
    sandbox_provider: SandboxProvider,
    sandbox_id: str | None,
) -> None:
    destroy = getattr(sandbox_provider, "destroy", None)
    if sandbox_id is None or not callable(destroy):
        return
    try:
        await _run_blocking_completion_safe(destroy, sandbox_id)
    except BaseException:
        logger.exception("Failed to discard sandbox after upload synchronization divergence")


def _open_upload_write_targets(
    uploads_dir: os.PathLike[str] | str,
    display_filename: str,
    *,
    uploads_fd: int,
):
    file_path, fh = open_upload_file_no_symlink(
        uploads_dir,
        display_filename,
        directory_fd=uploads_fd,
    )
    try:
        snapshot = tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix="deerflow-upload-snapshot-",
            suffix=Path(display_filename).suffix,
            delete=False,
        )
    except BaseException:
        fh.close()
        try:
            os.unlink(display_filename, dir_fd=uploads_fd)
        except FileNotFoundError:
            pass
        raise
    return file_path, fh, snapshot, Path(snapshot.name)


def _write_upload_chunk(fh, snapshot, chunk: bytes) -> None:
    fh.write(chunk)
    snapshot.write(chunk)


def _finish_upload_writes(fh, snapshot) -> None:
    try:
        snapshot.flush()
        os.fsync(snapshot.fileno())
    finally:
        try:
            fh.close()
        finally:
            snapshot.close()


def _abort_upload_writes(fh, snapshot, display_filename: str, snapshot_path: Path, *, uploads_fd: int) -> None:
    try:
        fh.close()
    finally:
        snapshot.close()
    try:
        os.unlink(display_filename, dir_fd=uploads_fd)
    except FileNotFoundError:
        pass
    snapshot_path.unlink(missing_ok=True)


def _delete_upload_snapshots(snapshot_path: Path) -> None:
    snapshot_path.unlink(missing_ok=True)
    snapshot_path.with_suffix(".md").unlink(missing_ok=True)


async def _write_upload_file_with_limits(
    file: UploadFile,
    *,
    uploads_dir: os.PathLike[str] | str,
    uploads_fd: int,
    display_filename: str,
    max_single_file_size: int,
    max_total_size: int,
    total_size: int,
) -> tuple[os.PathLike[str] | str, int, int, Path]:
    file_size = 0
    opened, deferred_cancellation = await _run_blocking_with_deferred_cancellation(
        _open_upload_write_targets,
        uploads_dir,
        display_filename,
        uploads_fd=uploads_fd,
    )
    file_path, fh, snapshot, snapshot_path = opened
    if deferred_cancellation is not None:
        await _run_blocking_completion_safe(
            _abort_upload_writes,
            fh,
            snapshot,
            display_filename,
            snapshot_path,
            uploads_fd=uploads_fd,
        )
        raise deferred_cancellation
    try:
        while chunk := await file.read(UPLOAD_CHUNK_SIZE):
            file_size += len(chunk)
            total_size += len(chunk)
            if file_size > max_single_file_size:
                raise HTTPException(status_code=413, detail=f"File too large: {display_filename}")
            if total_size > max_total_size:
                raise HTTPException(status_code=413, detail="Total upload size too large")
            await _run_blocking_completion_safe(_write_upload_chunk, fh, snapshot, chunk)
        await _run_blocking_completion_safe(_finish_upload_writes, fh, snapshot)
    except BaseException:
        await _run_blocking_completion_safe(
            _abort_upload_writes,
            fh,
            snapshot,
            display_filename,
            snapshot_path,
            uploads_fd=uploads_fd,
        )
        raise
    return file_path, file_size, total_size, snapshot_path


def _auto_convert_documents_enabled(app_config: AppConfig) -> bool:
    """Return whether automatic host-side document conversion is enabled.

    The secure default is disabled unless an operator explicitly opts in via
    uploads.auto_convert_documents in config.yaml.
    """
    try:
        raw = _get_uploads_config_value(app_config, "auto_convert_documents", False)
        if isinstance(raw, str):
            return raw.strip().lower() in {"1", "true", "yes", "on"}
        return bool(raw)
    except Exception:
        return False


def _list_and_enrich_uploaded_files(uploads_dir: os.PathLike[str] | str, thread_id: str) -> dict:
    result = list_files_in_dir(uploads_dir)
    enrich_file_listing(result, thread_id)
    return result


@router.post("", response_model=UploadResponse)
@require_permission(
    "threads",
    "write",
    owner_check=True,
    require_existing=True,
    thread_write_guard=True,
)
async def upload_files(
    thread_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    config: AppConfig = Depends(get_config),
) -> UploadResponse:
    effective_user_id = get_request_storage_user_id(request)
    async with _upload_mutation_lock(thread_id, effective_user_id):
        try:
            uploads_dir = ensure_uploads_dir(
                thread_id,
                user_id=effective_user_id,
            )
        except (FileNotFoundError, UnsafePathError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        lock_handle = await _acquire_upload_file_lock(uploads_dir)
        try:
            try:
                uploads_fd = open_directory_no_symlinks(uploads_dir)
            except (FileNotFoundError, UnsafePathError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            try:
                return await _upload_files_locked(
                    thread_id,
                    request,
                    files=files,
                    config=config,
                    effective_user_id=effective_user_id,
                    uploads_dir=uploads_dir,
                    uploads_fd=uploads_fd,
                )
            finally:
                os.close(uploads_fd)
        finally:
            await _release_upload_file_lock(lock_handle)


async def _upload_files_locked(
    thread_id: str,
    request: Request,
    files: list[UploadFile],
    config: AppConfig,
    *,
    effective_user_id: str,
    uploads_dir: Path,
    uploads_fd: int,
) -> UploadResponse:
    """Upload multiple files to a thread's uploads directory."""
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    limits = _get_upload_limits(config)
    if len(files) > limits.max_files:
        raise HTTPException(status_code=413, detail=f"Too many files: maximum is {limits.max_files}")

    sandbox_uploads = get_paths().sandbox_uploads_dir(thread_id, user_id=effective_user_id)
    uploaded_files = []
    written_paths = []
    sandbox_sync_targets = []
    skipped_files = []
    total_size = 0
    # Track filenames within this request so duplicate form parts do not
    # silently truncate each other. Existing uploads keep the historical
    # overwrite behavior for a single replacement upload.
    seen_filenames: set[str] = set()
    prepared_files: list[tuple[UploadFile, str, str]] = []
    for file in files:
        if not file.filename:
            skipped_files.append("<unnamed>")
            continue
        try:
            original_filename = normalize_filename(file.filename)
            safe_filename = claim_unique_filename(
                original_filename,
                seen_filenames,
            )
        except ValueError:
            logger.warning("Skipping file with unsafe filename: %r", file.filename)
            skipped_files.append(file.filename)
            continue
        prepared_files.append((file, original_filename, safe_filename))
    explicit_filenames = {safe_filename for _, _, safe_filename in prepared_files}
    generated_filenames = set(explicit_filenames)
    generated_filenames.update(await asyncio.to_thread(os.listdir, uploads_dir))

    sandbox_provider = get_sandbox_provider()
    sync_to_sandbox = not _uses_thread_data_mounts(sandbox_provider)
    adjust_upload_permissions = _needs_upload_permission_adjustment(sandbox_provider)
    sandbox = None
    sandbox_id: str | None = None

    async def release_sandbox_lease() -> None:
        nonlocal sandbox_id
        if sandbox_id is None:
            return
        acquired_id = sandbox_id
        sandbox_id = None
        try:
            await asyncio.to_thread(sandbox_provider.release, acquired_id)
        except Exception:
            logger.warning(
                "Failed to release sandbox lease %s after upload",
                acquired_id,
                exc_info=True,
            )

    if sync_to_sandbox:
        acquire_async = getattr(type(sandbox_provider), "acquire_async", None)
        if callable(acquire_async):
            sandbox_id = await sandbox_provider.acquire_async(
                thread_id,
                user_id=effective_user_id,
            )
        else:
            sandbox_id = await asyncio.to_thread(
                sandbox_provider.acquire,
                thread_id,
                user_id=effective_user_id,
            )
        sandbox = sandbox_provider.get(sandbox_id)
        if sandbox is None:
            await release_sandbox_lease()
            raise HTTPException(status_code=500, detail="Failed to acquire sandbox")

    auto_convert_documents = _auto_convert_documents_enabled(config)
    backup_result, deferred_cancellation = await _run_blocking_with_deferred_cancellation(
        _create_pinned_upload_subdirectory,
        uploads_dir,
        uploads_fd,
        prefix=".deerflow-upload-backup-",
    )
    backup_dir, backup_fd = backup_result
    if deferred_cancellation is not None:
        await _run_blocking_completion_safe(
            _discard_upload_backups,
            backup_dir,
            uploads_fd=uploads_fd,
            backup_fd=backup_fd,
        )
        await release_sandbox_lease()
        raise deferred_cancellation
    backups: dict[Path, Path] = {}

    for file, original_filename, safe_filename in prepared_files:
        written_start = len(written_paths)
        sync_targets_start = len(sandbox_sync_targets)
        total_size_before = total_size
        backups_before = set(backups)
        attempted_generated_paths: list[Path] = []
        snapshot_path: Path | None = None
        try:
            await _run_blocking_completion_safe(
                _backup_existing_upload,
                Path(uploads_dir) / safe_filename,
                backup_dir,
                backups,
                uploads_fd=uploads_fd,
                backup_fd=backup_fd,
            )
            file_path, file_size, total_size, snapshot_path = await _write_upload_file_with_limits(
                file,
                uploads_dir=uploads_dir,
                uploads_fd=uploads_fd,
                display_filename=safe_filename,
                max_single_file_size=limits.max_file_size,
                max_total_size=limits.max_total_size,
                total_size=total_size,
            )
            written_paths.append(file_path)

            virtual_path = upload_virtual_path(safe_filename)

            if sync_to_sandbox:
                sandbox_sync_targets.append((file_path, virtual_path, False))

            file_info = {
                "filename": safe_filename,
                "size": file_size,
                "path": str(sandbox_uploads / safe_filename),
                "virtual_path": virtual_path,
                "artifact_url": upload_artifact_url(thread_id, safe_filename),
            }
            if safe_filename != original_filename:
                file_info["original_filename"] = original_filename

            logger.info(f"Saved file: {safe_filename} ({file_size} bytes) to {file_info['path']}")

            file_path = Path(file_path)
            file_ext = file_path.suffix.lower()
            ocr_path = ocr_sidecar_path(file_path)
            create_ocr_sidecar = is_supported_image_path(file_path) and ocr_path.name not in explicit_filenames
            if create_ocr_sidecar:
                await _run_blocking_completion_safe(
                    _backup_existing_upload,
                    ocr_path,
                    backup_dir,
                    backups,
                    uploads_fd=uploads_fd,
                    backup_fd=backup_fd,
                )
                attempted_generated_paths.append(ocr_path)
            ocr_text = await asyncio.to_thread(extract_image_text, snapshot_path) if create_ocr_sidecar else None
            if create_ocr_sidecar and ocr_text:
                ocr_path, deferred_cancellation = await _run_blocking_with_deferred_cancellation(
                    write_upload_file_no_symlink,
                    Path(uploads_dir),
                    ocr_path.name,
                    ocr_text.encode("utf-8"),
                    directory_fd=uploads_fd,
                    allow_reserved_ocr=True,
                )
                written_paths.append(ocr_path)
                if deferred_cancellation is not None:
                    raise deferred_cancellation
                ocr_virtual_path = upload_virtual_path(ocr_path.name)

                file_info["ocr_file"] = ocr_path.name
                file_info["ocr_path"] = str(sandbox_uploads / ocr_path.name)
                file_info["ocr_virtual_path"] = ocr_virtual_path
                file_info["ocr_artifact_url"] = upload_artifact_url(thread_id, ocr_path.name)
            if create_ocr_sidecar and sync_to_sandbox:
                sandbox_sync_targets.append(
                    (
                        ocr_path,
                        upload_virtual_path(ocr_path.name),
                        not bool(ocr_text),
                    )
                )

            create_markdown_companion = file_ext in CONVERTIBLE_EXTENSIONS
            if create_markdown_companion:
                default_markdown_target = file_path.with_suffix(".md")
                markdown_filename = claim_unique_filename(
                    default_markdown_target.name,
                    generated_filenames,
                )
                markdown_target = file_path.with_name(markdown_filename)
                await _run_blocking_completion_safe(
                    _backup_existing_upload,
                    markdown_target,
                    backup_dir,
                    backups,
                    uploads_fd=uploads_fd,
                    backup_fd=backup_fd,
                )
                attempted_generated_paths.append(markdown_target)
                md_path = await convert_file_to_markdown(snapshot_path) if auto_convert_documents else None
                if md_path:
                    converted_path = Path(md_path)
                    converted_bytes = await asyncio.to_thread(converted_path.read_bytes)
                    md_path, deferred_cancellation = await _run_blocking_with_deferred_cancellation(
                        write_upload_file_no_symlink,
                        Path(uploads_dir),
                        markdown_target.name,
                        converted_bytes,
                        directory_fd=uploads_fd,
                    )
                    written_paths.append(md_path)
                    if deferred_cancellation is not None:
                        raise deferred_cancellation
                    md_virtual_path = upload_virtual_path(md_path.name)

                    file_info["markdown_file"] = md_path.name
                    file_info["markdown_path"] = str(sandbox_uploads / md_path.name)
                    file_info["markdown_virtual_path"] = md_virtual_path
                    file_info["markdown_artifact_url"] = upload_artifact_url(thread_id, md_path.name)
                else:
                    await _run_blocking_completion_safe(
                        _cleanup_uploaded_paths,
                        [markdown_target],
                        directory_fd=uploads_fd,
                    )
                if sync_to_sandbox:
                    sandbox_sync_targets.append(
                        (
                            markdown_target,
                            upload_virtual_path(markdown_target.name),
                            not bool(md_path),
                        )
                    )

            uploaded_files.append(file_info)

        except HTTPException as e:
            await _run_blocking_completion_safe(
                _cleanup_uploaded_paths,
                written_paths,
                directory_fd=uploads_fd,
            )
            await _run_blocking_completion_safe(
                _restore_upload_backups,
                backups,
                uploads_fd=uploads_fd,
                backup_fd=backup_fd,
            )
            await _run_blocking_completion_safe(
                _discard_upload_backups,
                backup_dir,
                uploads_fd=uploads_fd,
                backup_fd=backup_fd,
            )
            raise e
        except UnsafeUploadPathError as e:
            logger.warning("Skipping upload with unsafe destination %s: %s", file.filename, e)
            current_written = written_paths[written_start:]
            await _run_blocking_completion_safe(
                _cleanup_uploaded_paths,
                [*current_written, *attempted_generated_paths],
                directory_fd=uploads_fd,
            )
            del written_paths[written_start:]
            del sandbox_sync_targets[sync_targets_start:]
            total_size = total_size_before
            await _run_blocking_completion_safe(
                _restore_upload_backups,
                backups,
                set(backups) - backups_before,
                uploads_fd=uploads_fd,
                backup_fd=backup_fd,
            )
            skipped_files.append(safe_filename)
            continue
        except asyncio.CancelledError:
            await _run_blocking_completion_safe(
                _cleanup_uploaded_paths,
                written_paths,
                directory_fd=uploads_fd,
            )
            await _run_blocking_completion_safe(
                _restore_upload_backups,
                backups,
                uploads_fd=uploads_fd,
                backup_fd=backup_fd,
            )
            await _run_blocking_completion_safe(
                _discard_upload_backups,
                backup_dir,
                uploads_fd=uploads_fd,
                backup_fd=backup_fd,
            )
            raise
        except Exception:
            logger.exception("Failed to upload file %r", file.filename)
            await _run_blocking_completion_safe(
                _cleanup_uploaded_paths,
                written_paths,
                directory_fd=uploads_fd,
            )
            await _run_blocking_completion_safe(
                _restore_upload_backups,
                backups,
                uploads_fd=uploads_fd,
                backup_fd=backup_fd,
            )
            await _run_blocking_completion_safe(
                _discard_upload_backups,
                backup_dir,
                uploads_fd=uploads_fd,
                backup_fd=backup_fd,
            )
            raise HTTPException(status_code=500, detail="Failed to upload file")
        finally:
            if snapshot_path is not None:
                await _run_blocking_completion_safe(
                    _delete_upload_snapshots,
                    snapshot_path,
                )

    # Uploaded files are created with 0o600 permissions (owner read/write only).
    # Only providers that cross a host/container user boundary need broader
    # read bits; local per-thread sandboxes keep the stricter mode.
    try:
        attempted_remote_syncs: list[tuple[Path, str]] = []
        if adjust_upload_permissions:
            for file_path in written_paths:
                await _run_blocking_completion_safe(
                    _make_file_sandbox_readable,
                    file_path,
                    directory_fd=uploads_fd,
                )

        if sync_to_sandbox:
            for file_path, virtual_path, delete_remote in sandbox_sync_targets:
                file_path = Path(file_path)
                if delete_remote:
                    attempted_remote_syncs.append((file_path, virtual_path))
                    await _run_blocking_completion_safe(
                        _remove_sandbox_file,
                        sandbox,
                        virtual_path,
                    )
                    continue
                if adjust_upload_permissions:
                    await _run_blocking_completion_safe(
                        _make_file_sandbox_writable,
                        file_path,
                        directory_fd=uploads_fd,
                    )
                content = await asyncio.to_thread(
                    _read_regular_file_no_symlink,
                    file_path,
                    directory_fd=uploads_fd,
                )
                attempted_remote_syncs.append((file_path, virtual_path))
                await _run_blocking_completion_safe(
                    sandbox.update_file,
                    virtual_path,
                    content,
                )
    except asyncio.CancelledError:
        await _run_blocking_completion_safe(
            _cleanup_uploaded_paths,
            written_paths,
            directory_fd=uploads_fd,
        )
        await _run_blocking_completion_safe(
            _restore_upload_backups,
            backups,
            uploads_fd=uploads_fd,
            backup_fd=backup_fd,
        )
        try:
            await _restore_remote_sync_targets(
                sandbox,
                attempted_remote_syncs,
                directory_fd=uploads_fd,
            )
        except BaseException:
            logger.exception("Failed to compensate sandbox files after cancelled upload")
            await _discard_inconsistent_sandbox(sandbox_provider, sandbox_id)
        await _run_blocking_completion_safe(
            _discard_upload_backups,
            backup_dir,
            uploads_fd=uploads_fd,
            backup_fd=backup_fd,
        )
        raise
    except Exception as exc:
        logger.exception("Failed to synchronize uploaded files to sandbox")
        await _run_blocking_completion_safe(
            _cleanup_uploaded_paths,
            written_paths,
            directory_fd=uploads_fd,
        )
        await _run_blocking_completion_safe(
            _restore_upload_backups,
            backups,
            uploads_fd=uploads_fd,
            backup_fd=backup_fd,
        )
        try:
            await _restore_remote_sync_targets(
                sandbox,
                attempted_remote_syncs,
                directory_fd=uploads_fd,
            )
        except BaseException:
            logger.exception("Failed to compensate sandbox files after upload error")
            await _discard_inconsistent_sandbox(sandbox_provider, sandbox_id)
        await _run_blocking_completion_safe(
            _discard_upload_backups,
            backup_dir,
            uploads_fd=uploads_fd,
            backup_fd=backup_fd,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to synchronize uploaded files",
        ) from exc
    finally:
        await release_sandbox_lease()

    await _run_blocking_completion_safe(
        _discard_upload_backups,
        backup_dir,
        uploads_fd=uploads_fd,
        backup_fd=backup_fd,
    )

    message = f"Successfully uploaded {len(uploaded_files)} file(s)"
    if skipped_files:
        message += f"; skipped {len(skipped_files)} unsafe file(s)"

    return UploadResponse(
        success=not skipped_files,
        files=uploaded_files,
        message=message,
        skipped_files=skipped_files,
    )


@router.get("/limits", response_model=UploadLimits)
@require_permission("threads", "read", owner_check=True)
async def get_upload_limits(
    thread_id: str,
    request: Request,
    config: AppConfig = Depends(get_config),
) -> UploadLimits:
    """Return upload limits used by the gateway for this thread."""
    return _get_upload_limits(config)


@router.get("/list", response_model=UploadListResponse)
@require_permission("threads", "read", owner_check=True)
async def list_uploaded_files(thread_id: str, request: Request) -> UploadListResponse:
    effective_user_id = get_request_storage_user_id(request)
    async with _upload_mutation_lock(thread_id, effective_user_id):
        return await _list_uploaded_files_locked(thread_id, request)


async def _list_uploaded_files_locked(thread_id: str, request: Request) -> UploadListResponse:
    """List all files in a thread's uploads directory."""
    effective_user_id = get_request_storage_user_id(request)
    try:
        uploads_dir = get_uploads_dir(thread_id, user_id=effective_user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    result = await asyncio.to_thread(
        _list_and_enrich_uploaded_files,
        uploads_dir,
        thread_id,
    )

    # Gateway additionally includes the sandbox-relative path.
    sandbox_uploads = get_paths().sandbox_uploads_dir(thread_id, user_id=effective_user_id)
    for f in result["files"]:
        f["path"] = str(sandbox_uploads / f["filename"])

    return UploadListResponse(**result)


@router.delete("/{filename}")
@require_permission(
    "threads",
    "delete",
    owner_check=True,
    require_existing=True,
    thread_write_guard=True,
)
async def delete_uploaded_file(thread_id: str, filename: str, request: Request) -> dict:
    effective_user_id = get_request_storage_user_id(request)
    async with _upload_mutation_lock(thread_id, effective_user_id):
        try:
            uploads_dir = get_uploads_dir(
                thread_id,
                user_id=effective_user_id,
            )
            safe_filename = normalize_filename(filename)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        lock_handle = await _acquire_upload_file_lock(uploads_dir)
        try:
            try:
                uploads_fd = open_directory_no_symlinks(uploads_dir)
            except FileNotFoundError as exc:
                raise HTTPException(
                    status_code=404,
                    detail=f"File not found: {safe_filename}",
                ) from exc
            except UnsafePathError as exc:
                raise HTTPException(status_code=400, detail="Invalid upload path") from exc
            try:
                return await _delete_uploaded_file_locked(
                    thread_id,
                    filename,
                    request,
                    effective_user_id=effective_user_id,
                    uploads_dir=Path(uploads_dir),
                    uploads_fd=uploads_fd,
                    safe_filename=safe_filename,
                )
            finally:
                os.close(uploads_fd)
        finally:
            await _release_upload_file_lock(lock_handle)


async def _delete_uploaded_file_locked(
    thread_id: str,
    filename: str,
    request: Request,
    *,
    effective_user_id: str,
    uploads_dir: Path,
    uploads_fd: int,
    safe_filename: str,
) -> dict:
    """Delete a file from a thread's uploads directory."""
    file_path = Path(uploads_dir) / safe_filename
    delete_targets = [file_path, ocr_sidecar_path(file_path)]

    snapshots: dict[Path, bytes] = {}
    for target in delete_targets:
        try:
            snapshots[target] = await asyncio.to_thread(
                _read_regular_file_no_symlink,
                target,
                directory_fd=uploads_fd,
            )
        except FileNotFoundError:
            continue
        except (OSError, UnsafeUploadPathError) as exc:
            raise HTTPException(
                status_code=400,
                detail="Invalid upload path",
            ) from exc

    sandbox_provider = get_sandbox_provider()
    sync_to_sandbox = not _uses_thread_data_mounts(sandbox_provider)
    sandbox = None
    sandbox_id: str | None = None
    if sync_to_sandbox:
        try:
            acquire_async = getattr(type(sandbox_provider), "acquire_async", None)
            if callable(acquire_async):
                sandbox_id = await sandbox_provider.acquire_async(
                    thread_id,
                    user_id=effective_user_id,
                )
            else:
                sandbox_id = await asyncio.to_thread(
                    sandbox_provider.acquire,
                    thread_id,
                    user_id=effective_user_id,
                )
            sandbox = sandbox_provider.get(sandbox_id)
            if sandbox is None:
                raise RuntimeError("Failed to acquire sandbox")
        except Exception as exc:
            logger.exception(
                "Failed to acquire sandbox before deleting upload %r",
                safe_filename,
            )
            raise HTTPException(
                status_code=500,
                detail="Failed to delete file",
            ) from exc

    deferred_cancellation: asyncio.CancelledError | None = None
    try:
        response, deferred_cancellation = await _run_blocking_with_deferred_cancellation(
            delete_file_safe,
            uploads_dir,
            safe_filename,
            directory_fd=uploads_fd,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {safe_filename}")
    except PathTraversalError:
        raise HTTPException(status_code=400, detail="Invalid path")
    except Exception:
        logger.exception("Failed to delete uploaded file %r", safe_filename)
        raise HTTPException(status_code=500, detail="Failed to delete file")

    if not sync_to_sandbox:
        if deferred_cancellation is not None:
            raise deferred_cancellation
        return response

    attempted_remote_deletes: list[tuple[Path, str]] = []
    try:
        for target in delete_targets:
            virtual_path = upload_virtual_path(target.name)
            attempted_remote_deletes.append((target, virtual_path))
            await _run_blocking_completion_safe(
                _remove_sandbox_file,
                sandbox,
                virtual_path,
            )
    except BaseException as exc:
        restore_failed = False
        for target, content in snapshots.items():
            try:
                await asyncio.to_thread(
                    write_upload_file_no_symlink,
                    Path(uploads_dir),
                    target.name,
                    content,
                    directory_fd=uploads_fd,
                )
            except BaseException:
                restore_failed = True
                logger.exception(
                    "Failed to restore host upload %s after remote delete error",
                    target,
                )
        try:
            await _restore_remote_sync_targets(
                sandbox,
                attempted_remote_deletes,
                directory_fd=uploads_fd,
            )
        except BaseException:
            restore_failed = True
            logger.exception("Failed to restore sandbox uploads after remote delete error")
        if restore_failed:
            try:
                recovery_dir = await _run_blocking_completion_safe(
                    _persist_failed_delete_recovery,
                    Path(uploads_dir),
                    snapshots,
                    directory_fd=uploads_fd,
                )
                logger.error("Retained failed upload delete recovery data in %s", recovery_dir)
            except BaseException:
                logger.exception("Failed to persist upload delete recovery data")
            await _discard_inconsistent_sandbox(sandbox_provider, sandbox_id)
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise HTTPException(status_code=500, detail="Failed to delete file") from exc

    if deferred_cancellation is not None:
        raise deferred_cancellation
    return response
