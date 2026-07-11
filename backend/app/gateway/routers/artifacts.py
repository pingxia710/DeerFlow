import asyncio
import errno
import logging
import mimetypes
import os
import stat
import weakref
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response

from app.gateway.authz import require_permission
from app.gateway.path_utils import get_request_storage_user_id, resolve_thread_virtual_path
from deerflow.config.paths import UnsafePathError, open_file_no_symlinks
from deerflow.utils.cancellation import await_task_through_repeated_cancellation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["artifacts"])

ACTIVE_CONTENT_MIME_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "image/svg+xml",
}
ARTIFACT_SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff"}

MAX_SKILL_ARCHIVE_MEMBER_BYTES = 16 * 1024 * 1024
_SKILL_ARCHIVE_READ_CHUNK_SIZE = 64 * 1024


def _close_fd(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass


def _open_regular_artifact(path: Path) -> tuple[int, os.stat_result]:
    """Open one validated artifact inode without following a replaced symlink."""
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        fd = open_file_no_symlinks(path, flags)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Artifact not found: {path.name}") from exc
    except UnsafePathError as exc:
        raise HTTPException(status_code=400, detail=f"Unsafe artifact path: {path.name}") from exc
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.EISDIR, errno.ENOTDIR, errno.ENXIO, errno.EAGAIN}:
            raise HTTPException(status_code=400, detail=f"Unsafe artifact path: {path.name}") from exc
        raise
    file_stat = os.fstat(fd)
    if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
        _close_fd(fd)
        raise HTTPException(status_code=400, detail=f"Path is not a safe regular file: {path.name}")
    return fd, file_stat


class _DuplicatingFileDescriptor:
    """Give each ``open()`` call ownership of a fresh descriptor."""

    def __init__(self, fd: int) -> None:
        self.fd = fd

    def __index__(self) -> int:
        return os.dup(self.fd)


class _OpenedFileResponse(FileResponse):
    """FileResponse bound to an already-opened inode instead of a mutable path."""

    def __init__(self, fd: int, *, stat_result: os.stat_result, **kwargs) -> None:
        super().__init__(_DuplicatingFileDescriptor(fd), stat_result=stat_result, **kwargs)
        self._fd_finalizer = weakref.finalize(self, _close_fd, fd)

    async def __call__(self, scope, receive, send) -> None:
        # ``http.response.pathsend`` accepts path names, not descriptors.
        extensions = dict(scope.get("extensions", {}))
        extensions.pop("http.response.pathsend", None)
        try:
            await super().__call__({**scope, "extensions": extensions}, receive, send)
        finally:
            self._fd_finalizer()


def _build_content_disposition(disposition_type: str, filename: str) -> str:
    """Build an RFC 5987 encoded Content-Disposition header value."""
    return f"{disposition_type}; filename*=UTF-8''{quote(filename)}"


def _artifact_headers(extra_headers: dict[str, str] | None = None) -> dict[str, str]:
    headers = dict(ARTIFACT_SECURITY_HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    return headers


def _build_attachment_headers(filename: str, extra_headers: dict[str, str] | None = None) -> dict[str, str]:
    return _artifact_headers({"Content-Disposition": _build_content_disposition("attachment", filename), **(extra_headers or {})})


def is_text_file_by_content(path: Path, sample_size: int = 8192) -> bool:
    """Check if file is text by examining content for null bytes."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(sample_size)
            # Text files shouldn't contain null bytes
            return _looks_like_text(chunk)
    except Exception:
        return False


def _looks_like_text(content: bytes) -> bool:
    return b"\x00" not in content


def _read_skill_archive_member(zip_ref: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """Read a .skill archive member while enforcing an uncompressed size cap."""
    if info.file_size > MAX_SKILL_ARCHIVE_MEMBER_BYTES:
        raise HTTPException(status_code=413, detail="Skill archive member is too large to preview")

    chunks: list[bytes] = []
    total_read = 0
    with zip_ref.open(info, "r") as src:
        while chunk := src.read(_SKILL_ARCHIVE_READ_CHUNK_SIZE):
            total_read += len(chunk)
            if total_read > MAX_SKILL_ARCHIVE_MEMBER_BYTES:
                raise HTTPException(status_code=413, detail="Skill archive member is too large to preview")
            chunks.append(chunk)
    return b"".join(chunks)


def _extract_file_from_skill_archive(zip_path, internal_path: str) -> bytes | None:
    """Extract a file from a .skill ZIP archive.

    Args:
        zip_path: Path to the .skill file (ZIP archive).
        internal_path: Path to the file inside the archive (e.g., "SKILL.md").

    Returns:
        The file content as bytes, or None if not found.
    """
    if not zipfile.is_zipfile(zip_path):
        return None

    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            # List all files in the archive
            infos_by_name = {info.filename: info for info in zip_ref.infolist()}

            # Try direct path first
            if internal_path in infos_by_name:
                return _read_skill_archive_member(zip_ref, infos_by_name[internal_path])

            # Try with any top-level directory prefix (e.g., "skill-name/SKILL.md")
            for name, info in infos_by_name.items():
                if name.endswith("/" + internal_path) or name == internal_path:
                    return _read_skill_archive_member(zip_ref, info)

            # Not found
            return None
    except (zipfile.BadZipFile, KeyError):
        return None


def _load_skill_archive_member(actual_skill_path: Path, skill_file_path: str, internal_path: str) -> tuple[bytes, str | None]:
    """Worker-thread body for the ``.skill`` branch of ``get_artifact``.

    The ``exists`` / ``is_file`` probes, the ZIP open+extract, and the MIME
    sniff (``mimetypes`` lazily stats the system MIME database on first use) are
    blocking filesystem IO and must stay off the event loop. Raised
    ``HTTPException``s propagate through ``asyncio.to_thread`` unchanged,
    preserving status codes.
    """
    fd, _ = _open_regular_artifact(actual_skill_path)
    with os.fdopen(fd, "rb") as file:
        content = _extract_file_from_skill_archive(file, internal_path)
    if content is None:
        raise HTTPException(status_code=404, detail=f"File '{internal_path}' not found in skill archive")
    mime_type, _ = mimetypes.guess_type(internal_path)
    return content, mime_type


def _read_artifact_payload(actual_path: Path, path: str, download: bool) -> tuple[str, str | None, bytes | str | tuple[int, os.stat_result]]:
    """Worker-thread body for the regular branch of ``get_artifact``.

    Stat probes, MIME sniffing (``mimetypes`` lazily stats the system MIME
    database on first use), and full-file reads are all blocking filesystem IO.
    Returns a ``(kind, mime_type, payload)`` plan the handler turns into a
    response on the loop: ``("file", mime, None)`` (let ``FileResponse`` stream
    it), ``("text", mime, str)``, or ``("bytes", mime, bytes)``. Behavior/error
    codes match the previous inline logic.
    """
    fd, file_stat = _open_regular_artifact(actual_path)
    keep_open = False
    try:
        mime_type, _ = mimetypes.guess_type(actual_path)
        # Keep the descriptor open so the response streams the inode that was
        # validated here even if the sandbox replaces the visible path.
        if download or mime_type in ACTIVE_CONTENT_MIME_TYPES:
            keep_open = True
            return ("file", mime_type, (fd, file_stat))
        with os.fdopen(fd, "rb") as file:
            fd = -1
            content = file.read()
        if mime_type and mime_type.startswith("text/"):
            return ("text", mime_type, content.decode("utf-8"))
        if _looks_like_text(content):
            return ("text", mime_type, content.decode("utf-8"))
        return ("bytes", mime_type, content)
    finally:
        if fd >= 0 and not keep_open:
            _close_fd(fd)


async def _read_artifact_payload_completion_safe(actual_path: Path, path: str, download: bool):
    """Close a worker-opened descriptor before propagating cancellation."""
    task = asyncio.create_task(asyncio.to_thread(_read_artifact_payload, actual_path, path, download))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        try:
            result = await await_task_through_repeated_cancellation(task)
        except BaseException:
            pass
        else:
            if result[0] == "file":
                _close_fd(result[2][0])
        raise cancelled


@router.get(
    "/threads/{thread_id}/artifacts/{path:path}",
    summary="Get Artifact File",
    description="Retrieve an artifact file generated by the AI agent. Text and binary files can be viewed inline, while active web content is always downloaded.",
)
@require_permission("threads", "read", owner_check=True)
async def get_artifact(thread_id: str, path: str, request: Request, download: bool = False) -> Response:
    """Get an artifact file by its path.

    The endpoint automatically detects file types and returns appropriate content types.
    Use the `download` query parameter to force file download for non-active content.

    Args:
        thread_id: The thread ID.
        path: The artifact path with virtual prefix (e.g., mnt/user-data/outputs/file.txt).
        request: FastAPI request object (automatically injected).

    Returns:
        The file content as a FileResponse with appropriate content type:
        - Active content (HTML/XHTML/SVG): Served as download attachment
        - Text files: Plain text with proper MIME type
        - Binary files: Inline display with download option

    Raises:
        HTTPException:
            - 400 if path is invalid or not a file
            - 403 if access denied (path traversal detected)
            - 404 if file not found

    Query Parameters:
        download (bool): If true, forces attachment download for file types that are
            otherwise returned inline or as plain text. Active HTML/XHTML/SVG content
            is always downloaded regardless of this flag.

    Example:
        - Get text file inline: `/api/threads/abc123/artifacts/mnt/user-data/outputs/notes.txt`
        - Download file: `/api/threads/abc123/artifacts/mnt/user-data/outputs/data.csv?download=true`
        - Active web content such as `.html`, `.xhtml`, and `.svg` artifacts is always downloaded
    """
    storage_user_id = get_request_storage_user_id(request)

    # Check if this is a request for a file inside a .skill archive (e.g., xxx.skill/SKILL.md)
    if ".skill/" in path:
        # Split the path at ".skill/" to get the ZIP file path and internal path
        skill_marker = ".skill/"
        marker_pos = path.find(skill_marker)
        skill_file_path = path[: marker_pos + len(".skill")]  # e.g., "mnt/user-data/outputs/my-skill.skill"
        internal_path = path[marker_pos + len(skill_marker) :]  # e.g., "SKILL.md"

        actual_skill_path = await asyncio.to_thread(resolve_thread_virtual_path, thread_id, skill_file_path, user_id=storage_user_id)

        # Offload the stat probes + ZIP open/extract + MIME sniff (blocking filesystem IO).
        content, mime_type = await asyncio.to_thread(_load_skill_archive_member, actual_skill_path, skill_file_path, internal_path)

        # Add cache headers to avoid repeated ZIP extraction (cache for 5 minutes)
        cache_headers = _artifact_headers({"Cache-Control": "private, max-age=300"})
        download_name = Path(internal_path).name or actual_skill_path.stem
        if download or mime_type in ACTIVE_CONTENT_MIME_TYPES:
            return Response(content=content, media_type=mime_type or "application/octet-stream", headers=_build_attachment_headers(download_name, cache_headers))

        if mime_type and mime_type.startswith("text/"):
            return PlainTextResponse(content=content.decode("utf-8"), media_type=mime_type, headers=cache_headers)

        # Default to plain text for unknown types that look like text.
        if _looks_like_text(content):
            try:
                return PlainTextResponse(content=content.decode("utf-8"), media_type="text/plain", headers=cache_headers)
            except UnicodeDecodeError:
                pass

        headers = cache_headers if mime_type else _build_attachment_headers(download_name, cache_headers)
        return Response(content=content, media_type=mime_type or "application/octet-stream", headers=headers)

    actual_path = await asyncio.to_thread(resolve_thread_virtual_path, thread_id, path, user_id=storage_user_id)

    logger.info(f"Resolving artifact path: thread_id={thread_id}, requested_path={path}, actual_path={actual_path}")

    # Offload path stat + MIME sniff + file reads (all blocking filesystem IO).
    # Active content and explicit downloads are streamed by FileResponse, so the
    # worker only reports the kind; inline text/binary payloads are read in-thread.
    kind, mime_type, payload = await _read_artifact_payload_completion_safe(actual_path, path, download)

    if kind == "file":
        # Always force download for active content types to prevent script
        # execution in the application origin when users open generated artifacts.
        fd, file_stat = payload
        return _OpenedFileResponse(fd, stat_result=file_stat, filename=actual_path.name, media_type=mime_type, headers=_build_attachment_headers(actual_path.name))

    if kind == "text":
        return PlainTextResponse(content=payload, media_type=mime_type, headers=_artifact_headers())

    if mime_type:
        headers = _artifact_headers({"Content-Disposition": _build_content_disposition("inline", actual_path.name)})
        return Response(content=payload, media_type=mime_type, headers=headers)

    return Response(content=payload, media_type="application/octet-stream", headers=_build_attachment_headers(actual_path.name))
