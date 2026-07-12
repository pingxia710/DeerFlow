"""Shared upload management logic.

Pure business logic — no FastAPI/HTTP dependencies.
Both Gateway and Client delegate to these functions.
"""

import errno
import hashlib
import os
import shutil
import stat
import tempfile
import threading
import unicodedata
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote
from weakref import WeakValueDictionary

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]
    import msvcrt

from deerflow.config.paths import (
    VIRTUAL_PATH_PREFIX,
    UnsafePathError,
    ensure_directory_no_symlinks,
    get_paths,
    open_directory_no_symlinks,
    open_file_no_symlinks,
)
from deerflow.config.paths import (
    validate_thread_id as validate_runtime_thread_id,
)
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.utils.image_ocr import is_ocr_sidecar, ocr_sidecar_path

MAX_FILENAME_BYTES = 255
_UPLOAD_LOCAL_LOCKS: WeakValueDictionary[str, threading.Lock] = WeakValueDictionary()
_UPLOAD_LOCAL_LOCKS_GUARD = threading.Lock()


class PathTraversalError(ValueError):
    """Raised when a path escapes its allowed base directory."""


class UnsafeUploadPathError(ValueError):
    """Raised when an upload destination is not a safe regular file path."""


def _get_upload_local_lock(base_dir: Path) -> threading.Lock:
    key = str(base_dir)
    with _UPLOAD_LOCAL_LOCKS_GUARD:
        lock = _UPLOAD_LOCAL_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _UPLOAD_LOCAL_LOCKS[key] = lock
        return lock


def get_upload_control_dir(base_dir: Path) -> Path:
    """Return a host-only control directory outside the sandbox user-data tree."""
    absolute = validate_uploads_directory(base_dir)
    digest = hashlib.sha256(str(absolute).encode("utf-8")).hexdigest()[:20]
    control_parent = absolute.parent.parent if absolute.parent.name == "user-data" else absolute.parent
    control_dir = control_parent / ".deerflow-upload-control" / digest
    control_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    control_stat = os.lstat(control_dir)
    if not stat.S_ISDIR(control_stat.st_mode):
        raise UnsafeUploadPathError("Upload control path is not a directory")
    control_dir.chmod(0o700)
    return control_dir


def acquire_upload_transaction_lock(
    base_dir: Path,
) -> tuple[BinaryIO, threading.Lock]:
    """Acquire the owner/thread upload lock and return its open handle."""
    local_lock = _get_upload_local_lock(base_dir)
    local_lock.acquire()
    lock_path = get_upload_control_dir(base_dir) / "transaction.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = -1
    handle: BinaryIO | None = None
    try:
        fd = os.open(lock_path, flags, 0o600)
        lock_stat = os.fstat(fd)
        if not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_nlink != 1:
            raise UnsafeUploadPathError("Upload transaction lock is unsafe")
        handle = os.fdopen(fd, "r+b", closefd=True)
        fd = -1
        if fcntl is not None:
            fcntl.flock(handle, fcntl.LOCK_EX)
        else:  # pragma: no cover - Windows fallback
            if lock_stat.st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return handle, local_lock
    except BaseException:
        if fd >= 0:
            os.close(fd)
        elif handle is not None:
            handle.close()
        local_lock.release()
        raise


def release_upload_transaction_lock(
    lock_handle: tuple[BinaryIO, threading.Lock],
) -> None:
    """Release and close a handle returned by acquire_upload_transaction_lock."""
    handle, local_lock = lock_handle
    try:
        if fcntl is not None:
            fcntl.flock(handle, fcntl.LOCK_UN)
        else:  # pragma: no cover - Windows fallback
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        try:
            handle.close()
        finally:
            local_lock.release()


def validate_thread_id(thread_id: str) -> None:
    """Reject thread IDs containing characters unsafe for filesystem paths.

    Raises:
        ValueError: If thread_id is empty or contains unsafe characters.
    """
    validate_runtime_thread_id(thread_id)


def get_uploads_dir(thread_id: str, *, user_id: str | None = None) -> Path:
    """Return the uploads directory path for a thread (no side effects)."""
    validate_thread_id(thread_id)
    return get_paths().sandbox_uploads_dir(thread_id, user_id=user_id or get_effective_user_id())


def validate_uploads_directory(base_dir: Path) -> Path:
    """Reject a replaced/symlinked uploads directory or user-data parent."""
    absolute = base_dir.absolute()
    paths = [absolute.parent, absolute]
    if absolute.parent.name == "user-data":
        paths.insert(0, absolute.parent.parent)
    for path in paths:
        try:
            path_stat = os.lstat(path)
        except FileNotFoundError:
            raise UnsafeUploadPathError(f"Upload directory component is missing: {path.name}") from None
        if not stat.S_ISDIR(path_stat.st_mode):
            raise UnsafeUploadPathError(f"Upload directory component is unsafe: {path.name}")
    return absolute


def ensure_uploads_dir(thread_id: str, *, user_id: str | None = None) -> Path:
    """Return the uploads directory for a thread, creating it if needed."""
    base = get_uploads_dir(thread_id, user_id=user_id)
    try:
        return ensure_directory_no_symlinks(base)
    except UnsafePathError as exc:
        raise UnsafeUploadPathError(str(exc)) from exc


def normalize_filename(
    filename: str,
    *,
    allow_reserved_ocr: bool = False,
) -> str:
    """Sanitize a filename by extracting its basename.

    Strips any directory components and rejects traversal patterns.

    Args:
        filename: Raw filename from user input (may contain path components).

    Returns:
        Safe filename (basename only).

    Raises:
        ValueError: If filename is empty or resolves to a traversal pattern.
    """
    if not filename:
        raise ValueError("Filename is empty")
    safe = Path(filename).name
    if not safe or safe in {".", ".."}:
        raise ValueError(f"Filename is unsafe: {filename!r}")
    # Reject backslashes — on Linux Path.name keeps them as literal chars,
    # but they indicate a Windows-style path that should be stripped or rejected.
    if "\\" in safe:
        raise ValueError(f"Filename contains backslash: {filename!r}")
    if len(safe.encode("utf-8")) > MAX_FILENAME_BYTES:
        raise ValueError(f"Filename too long: {len(safe)} chars")
    if not allow_reserved_ocr and is_ocr_sidecar(Path(safe)):
        raise ValueError(f"Filename uses reserved OCR sidecar suffix: {safe!r}")
    return safe


def _filename_collision_key(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def claim_unique_filename(name: str, seen: set[str]) -> str:
    """Generate a unique filename by appending ``_N`` suffix on collision.

    Automatically adds the returned name to *seen* so callers don't need to.

    Args:
        name: Candidate filename.
        seen: Set of filenames already claimed (mutated in place).

    Returns:
        A filename not present in *seen* (already added to *seen*).
    """
    seen_keys = {_filename_collision_key(item) for item in seen}
    if _filename_collision_key(name) not in seen_keys:
        seen.add(name)
        return name
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 1
    while True:
        duplicate_suffix = f"_{counter}"
        stem_byte_budget = MAX_FILENAME_BYTES - len(f"{duplicate_suffix}{suffix}".encode())
        if stem_byte_budget < 0:
            raise ValueError("Filename extension leaves no room for a unique suffix")
        truncated_stem = stem.encode("utf-8")[:stem_byte_budget].decode("utf-8", errors="ignore")
        candidate = f"{truncated_stem}{duplicate_suffix}{suffix}"
        if _filename_collision_key(candidate) not in seen_keys:
            seen.add(candidate)
            return candidate
        counter += 1


def validate_path_traversal(path: Path, base: Path) -> None:
    """Verify that *path* is inside *base*.

    Raises:
        PathTraversalError: If a path traversal is detected.
    """
    try:
        path.resolve().relative_to(base.resolve())
    except ValueError:
        raise PathTraversalError("Path traversal detected") from None


def open_upload_file_no_symlink(
    base_dir: Path,
    filename: str,
    *,
    directory_fd: int | None = None,
    allow_reserved_ocr: bool = False,
) -> tuple[Path, object]:
    """Open an upload destination for safe streaming writes.

    Upload directories may be mounted into local sandboxes. A sandbox process can
    therefore leave a symlink at a future upload filename. Normal ``Path.write_bytes``
    follows that link and can overwrite files outside the uploads directory with
    gateway privileges. This helper rejects symlink destinations using ``O_NOFOLLOW``
    on POSIX. On Windows (which lacks ``O_NOFOLLOW``), it uses dual ``lstat`` checks
    and ``fstat`` validation after ``open()`` to reduce the TOCTOU window; this does
    not eliminate all races but makes exploitation significantly harder. Path-traversal
    validation prevents escapes from *base_dir* in both cases.
    """
    safe_name = normalize_filename(
        filename,
        allow_reserved_ocr=allow_reserved_ocr,
    )
    dest = base_dir / safe_name

    try:
        if directory_fd is None:
            st = os.lstat(dest)
        else:
            st = os.stat(
                safe_name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
    except FileNotFoundError:
        st = None

    if st is not None and not stat.S_ISREG(st.st_mode):
        raise UnsafeUploadPathError(f"Upload destination is not a regular file: {safe_name}")

    if directory_fd is None:
        validate_path_traversal(dest, base_dir)

    if hasattr(os, "O_NOFOLLOW"):
        # POSIX: walk every ancestor by descriptor, then open the final name
        # with O_NOFOLLOW. A sandbox cannot redirect the operation by swapping
        # an intermediate directory after validation.
        flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK

        try:
            if directory_fd is None:
                fd = open_file_no_symlinks(dest, flags, 0o600)
            else:
                fd = os.open(
                    safe_name,
                    flags,
                    0o600,
                    dir_fd=directory_fd,
                )
        except UnsafePathError as exc:
            raise UnsafeUploadPathError(f"Unsafe upload destination: {safe_name}") from exc
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.EISDIR, errno.ENOTDIR, errno.ENXIO, errno.EAGAIN}:
                raise UnsafeUploadPathError(f"Unsafe upload destination: {safe_name}") from exc
            raise

        try:
            opened_stat = os.fstat(fd)
            if not stat.S_ISREG(opened_stat.st_mode) or opened_stat.st_nlink != 1:
                raise UnsafeUploadPathError(f"Upload destination is not an exclusive regular file: {safe_name}")
            os.ftruncate(fd, 0)
            fh = os.fdopen(fd, "wb")
            fd = -1
        finally:
            if fd >= 0:
                os.close(fd)
        return dest, fh

    # Windows: no O_NOFOLLOW available. Uses a second lstat immediately before open()
    # to narrow the TOCTOU window, then fstat after open() as a further defence.
    # Note: a narrow race window remains between the pre-open lstat and open(); the
    # path-traversal check mitigates escapes from base_dir but cannot prevent an
    # attacker who can atomically replace dest with a symlink after the check.
    if st is not None and st.st_nlink > 1:
        raise UnsafeUploadPathError(f"Upload destination has multiple links: {safe_name}")

    flags = os.O_WRONLY | os.O_CREAT
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY

    try:
        pre_open_st = os.lstat(dest)
    except FileNotFoundError:
        pre_open_st = None

    if pre_open_st is not None and not stat.S_ISREG(pre_open_st.st_mode):
        raise UnsafeUploadPathError(f"Upload destination is not a regular file: {safe_name}")
    if pre_open_st is not None and pre_open_st.st_nlink > 1:
        raise UnsafeUploadPathError(f"Upload destination has multiple links: {safe_name}")

    try:
        fd = os.open(dest, flags, 0o600)
    except OSError as exc:
        if exc.errno in {errno.EISDIR, errno.ENOTDIR, errno.ENXIO, errno.EAGAIN}:
            raise UnsafeUploadPathError(f"Unsafe upload destination: {safe_name}") from exc
        raise

    try:
        opened_stat = os.fstat(fd)
        if not stat.S_ISREG(opened_stat.st_mode) or opened_stat.st_nlink > 1:
            raise UnsafeUploadPathError(f"Upload destination is not an exclusive regular file: {safe_name}")
        os.ftruncate(fd, 0)
        fh = os.fdopen(fd, "wb")
        fd = -1
    finally:
        if fd >= 0:
            os.close(fd)
    return dest, fh


def write_upload_file_no_symlink(
    base_dir: Path,
    filename: str,
    data: bytes,
    *,
    directory_fd: int | None = None,
    allow_reserved_ocr: bool = False,
) -> Path:
    """Atomically write upload bytes without following an unsafe target."""
    if directory_fd is not None:
        dest, fh = open_upload_file_no_symlink(
            base_dir,
            filename,
            directory_fd=directory_fd,
            allow_reserved_ocr=allow_reserved_ocr,
        )
        with fh:
            fh.write(data)
        return dest

    def write(output) -> None:
        output.write(data)

    return _replace_upload_atomically(
        base_dir,
        filename,
        write,
        allow_reserved_ocr=allow_reserved_ocr,
    )


def _replace_upload_atomically(
    base_dir: Path,
    filename: str,
    writer,
    *,
    allow_reserved_ocr: bool = False,
) -> Path:
    safe_name = normalize_filename(
        filename,
        allow_reserved_ocr=allow_reserved_ocr,
    )
    dest = base_dir / safe_name

    try:
        current = os.lstat(dest)
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
            raise UnsafeUploadPathError(f"Upload destination is not an exclusive regular file: {safe_name}")
    validate_path_traversal(dest, base_dir)

    stage_path = Path(
        tempfile.mkdtemp(
            prefix=".deerflow-upload-stage-",
            dir=get_upload_control_dir(base_dir),
        )
    )
    stage_stat = os.lstat(stage_path)
    payload_path = stage_path / "payload"
    use_dir_fds = all(function in os.supports_dir_fd for function in (os.open, os.rename, os.unlink))
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    stage_fd = -1
    base_fd = -1
    fd = -1
    try:
        if use_dir_fds:
            stage_fd = os.open(stage_path, directory_flags)
            base_fd = os.open(base_dir, directory_flags)
            fd = os.open(
                "payload",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=stage_fd,
            )
        else:  # pragma: no cover - exercised on Windows
            fd = os.open(
                payload_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        with os.fdopen(fd, "wb") as output:
            fd = -1
            writer(output)
        if use_dir_fds:
            os.replace(
                "payload",
                safe_name,
                src_dir_fd=stage_fd,
                dst_dir_fd=base_fd,
            )
        else:  # pragma: no cover - exercised on Windows
            os.replace(payload_path, dest)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        if stage_fd >= 0:
            try:
                os.unlink("payload", dir_fd=stage_fd)
            except FileNotFoundError:
                pass
        else:
            payload_path.unlink(missing_ok=True)
        raise
    finally:
        if stage_fd >= 0:
            os.close(stage_fd)
        if base_fd >= 0:
            os.close(base_fd)
        try:
            current_stage = os.lstat(stage_path)
        except FileNotFoundError:
            pass
        else:
            if current_stage.st_dev == stage_stat.st_dev and current_stage.st_ino == stage_stat.st_ino:
                try:
                    stage_path.rmdir()
                except OSError:
                    pass
    return dest


def write_ocr_sidecar_no_symlink(
    base_dir: Path,
    filename: str,
    data: bytes,
) -> Path:
    """Atomically write an internal OCR sidecar using the reserved suffix."""
    if not is_ocr_sidecar(Path(filename)):
        raise ValueError("OCR sidecar filename must use the reserved suffix")

    def write(output) -> None:
        output.write(data)

    return _replace_upload_atomically(
        base_dir,
        filename,
        write,
        allow_reserved_ocr=True,
    )


def copy_upload_file_no_symlink(
    base_dir: Path,
    filename: str,
    source: Path,
) -> Path:
    """Copy *source* into uploads without following a destination symlink."""
    destination = base_dir / normalize_filename(filename)
    try:
        if os.path.samefile(source, destination):
            raise shutil.SameFileError(source, destination, "Source and destination are the same file")
    except FileNotFoundError:
        pass

    with source.open("rb") as input_file:

        def copy(output) -> None:
            shutil.copyfileobj(input_file, output)

        return _replace_upload_atomically(base_dir, filename, copy)


def list_files_in_dir(directory: Path) -> dict:
    """List files (not directories) in *directory*.

    Args:
        directory: Directory to scan.

    Returns:
        Dict with "files" list (sorted by name) and "count".
        Each file entry has ``size`` as *int* (bytes).  Call
        :func:`enrich_file_listing` to add virtual / artifact URLs.
    """
    if not directory.is_dir():
        return {"files": [], "count": 0}

    files = []
    with os.scandir(directory) as entries:
        for entry in sorted(entries, key=lambda e: e.name):
            if not entry.is_file(follow_symlinks=False):
                continue
            if is_ocr_sidecar(Path(entry.name)):
                continue
            st = entry.stat(follow_symlinks=False)
            files.append(
                {
                    "filename": entry.name,
                    "size": st.st_size,
                    "path": entry.path,
                    "extension": Path(entry.name).suffix,
                    "modified": st.st_mtime,
                }
            )
    return {"files": files, "count": len(files)}


def delete_file_safe(
    base_dir: Path,
    filename: str,
    *,
    convertible_extensions: set[str] | None = None,
    directory_fd: int | None = None,
) -> dict:
    """Delete a file inside *base_dir* after path-traversal validation.

    Args:
        base_dir: Directory containing the file.
        filename: Name of file to delete.

    Returns:
        Dict with success and message.

    Raises:
        FileNotFoundError: If the file does not exist.
        PathTraversalError: If path traversal is detected.
    """
    # Preserve the public traversal-error contract before reducing the input to
    # the single upload filename used for descriptor-relative deletion.
    validate_path_traversal(base_dir / filename, base_dir)
    safe_name = normalize_filename(filename)
    file_path = base_dir / safe_name

    if hasattr(os, "O_NOFOLLOW") and os.unlink in os.supports_dir_fd:
        if directory_fd is None:
            try:
                operation_fd = open_directory_no_symlinks(base_dir)
            except UnsafePathError as exc:
                raise UnsafeUploadPathError(str(exc)) from exc
        else:
            operation_fd = os.dup(directory_fd)
        try:
            try:
                file_stat = os.stat(
                    safe_name,
                    dir_fd=operation_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                raise FileNotFoundError(f"File not found: {safe_name}") from None
            if stat.S_ISLNK(file_stat.st_mode):
                raise PathTraversalError("Path traversal detected")
            if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
                raise UnsafeUploadPathError(f"Upload target is not an exclusive regular file: {safe_name}")
            os.unlink(safe_name, dir_fd=operation_fd)

            companion_names: list[str] = []
            if convertible_extensions and file_path.suffix.lower() in convertible_extensions:
                companion_names.append(file_path.with_suffix(".md").name)
            companion_names.append(ocr_sidecar_path(file_path).name)
            for companion_name in companion_names:
                try:
                    os.unlink(companion_name, dir_fd=operation_fd)
                except FileNotFoundError:
                    pass
        finally:
            os.close(operation_fd)
        return {"success": True, "message": f"Deleted {safe_name}"}

    if not file_path.is_file():
        raise FileNotFoundError(f"File not found: {safe_name}")

    file_path.unlink()

    ocr_sidecar_path(file_path).unlink(missing_ok=True)

    return {"success": True, "message": f"Deleted {safe_name}"}


def upload_artifact_url(thread_id: str, filename: str) -> str:
    """Build the artifact URL for a file in a thread's uploads directory.

    *filename* is percent-encoded so that spaces, ``#``, ``?`` etc. are safe.
    """
    return f"/api/threads/{thread_id}/artifacts{VIRTUAL_PATH_PREFIX}/uploads/{quote(filename, safe='')}"


def upload_virtual_path(filename: str) -> str:
    """Build the virtual path for a file in the uploads directory."""
    return f"{VIRTUAL_PATH_PREFIX}/uploads/{filename}"


def enrich_file_listing(result: dict, thread_id: str) -> dict:
    """Add virtual paths and artifact URLs on a listing result.

    Mutates *result* in place and returns it for convenience.
    """
    for f in result["files"]:
        filename = f["filename"]
        f["virtual_path"] = upload_virtual_path(filename)
        f["artifact_url"] = upload_artifact_url(thread_id, filename)
    return result
