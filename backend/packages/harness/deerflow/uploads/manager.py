"""Shared upload management logic.

Pure business logic — no FastAPI/HTTP dependencies.
Both Gateway and Client delegate to these functions.
"""

import errno
import os
import stat
from pathlib import Path
from urllib.parse import quote

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


class PathTraversalError(ValueError):
    """Raised when a path escapes its allowed base directory."""


class UnsafeUploadPathError(ValueError):
    """Raised when an upload destination is not a safe regular file path."""


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


def ensure_uploads_dir(thread_id: str, *, user_id: str | None = None) -> Path:
    """Return the uploads directory for a thread, creating it if needed."""
    base = get_uploads_dir(thread_id, user_id=user_id)
    try:
        return ensure_directory_no_symlinks(base)
    except UnsafePathError as exc:
        raise UnsafeUploadPathError(str(exc)) from exc


def normalize_filename(filename: str) -> str:
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
    if len(safe.encode("utf-8")) > 255:
        raise ValueError(f"Filename too long: {len(safe)} chars")
    return safe


def claim_unique_filename(name: str, seen: set[str]) -> str:
    """Generate a unique filename by appending ``_N`` suffix on collision.

    Automatically adds the returned name to *seen* so callers don't need to.

    Args:
        name: Candidate filename.
        seen: Set of filenames already claimed (mutated in place).

    Returns:
        A filename not present in *seen* (already added to *seen*).
    """
    if name not in seen:
        seen.add(name)
        return name
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 1
    candidate = f"{stem}_{counter}{suffix}"
    while candidate in seen:
        counter += 1
        candidate = f"{stem}_{counter}{suffix}"
    seen.add(candidate)
    return candidate


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
    safe_name = normalize_filename(filename)
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
) -> Path:
    """Write upload bytes without following a pre-existing destination symlink."""
    dest, fh = open_upload_file_no_symlink(
        base_dir,
        filename,
        directory_fd=directory_fd,
    )
    with fh:
        fh.write(data)
    return dest


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

    If *convertible_extensions* is provided and the file's extension matches,
    the companion ``.md`` file is also removed (if it exists).

    Args:
        base_dir: Directory containing the file.
        filename: Name of file to delete.
        convertible_extensions: Lowercase extensions (e.g. ``{".pdf", ".docx"}``)
            whose companion markdown should be cleaned up.

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

    # Clean up companion markdown generated during upload conversion.
    if convertible_extensions and file_path.suffix.lower() in convertible_extensions:
        file_path.with_suffix(".md").unlink(missing_ok=True)
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
