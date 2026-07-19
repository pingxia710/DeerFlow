import errno
import hashlib
import logging
import os
import re
import shutil
import stat
from pathlib import Path, PureWindowsPath

from deerflow.config.runtime_paths import runtime_home

# Virtual path prefix seen by agents inside the sandbox
VIRTUAL_PATH_PREFIX = "/mnt/user-data"

_SAFE_THREAD_ID_RE = re.compile(r"[A-Za-z0-9_\-]+")
_SAFE_USER_ID_RE = re.compile(r"[A-Za-z0-9_\-]+")
_UNSAFE_USER_ID_CHAR_RE = re.compile(r"[^A-Za-z0-9_\-]")
_SAFE_USER_ID_DIGEST_HEX_LEN = 16
_MAX_PATH_COMPONENT_UTF8_BYTES = 255

logger = logging.getLogger(__name__)


class UnsafePathError(ValueError):
    """Raised when a filesystem path contains a symlink or unsafe component."""


def _absolute_lexical_path(path: str | os.PathLike[str]) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    candidate = Path(os.path.abspath(candidate))

    # macOS exposes system-owned aliases such as /var -> /private/var and
    # /tmp -> /private/tmp.  Canonicalize only that top-level component: doing
    # the same for a deeper component would follow an owner/thread-controlled
    # symlink and defeat the checks below.
    parts = candidate.parts[1:] if candidate.anchor else candidate.parts
    if parts:
        top_level = Path(candidate.anchor) / parts[0]
        try:
            if top_level.is_symlink():
                candidate = top_level.resolve(strict=True).joinpath(*parts[1:])
        except OSError:
            pass
    return candidate


def validate_no_symlink_components(
    path: str | os.PathLike[str],
    *,
    allow_missing: bool = False,
) -> Path:
    """Validate existing path components without resolving symlinks."""
    absolute = _absolute_lexical_path(path)
    current = Path(absolute.anchor)
    parts = absolute.parts[1:] if absolute.anchor else absolute.parts
    for part in parts:
        current /= part
        try:
            current_stat = os.lstat(current)
        except FileNotFoundError:
            if allow_missing:
                return absolute
            raise
        if stat.S_ISLNK(current_stat.st_mode):
            raise UnsafePathError(f"Refusing symlinked path component: {current}")
    return absolute


def _supports_secure_dir_fd() -> bool:
    return bool(hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY") and os.open in os.supports_dir_fd)


def open_directory_no_symlinks(
    path: str | os.PathLike[str],
    *,
    create: bool = False,
    mode: int = 0o755,
) -> int:
    """Open a directory by walking every component with ``O_NOFOLLOW``."""
    absolute = _absolute_lexical_path(path)
    if not _supports_secure_dir_fd():
        validate_no_symlink_components(
            absolute,
            allow_missing=create,
        )
        if create:
            absolute.mkdir(parents=True, exist_ok=True, mode=mode)
        return os.open(absolute, os.O_RDONLY)

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(absolute.anchor or os.sep, os.O_RDONLY | os.O_DIRECTORY)
    try:
        parts = absolute.parts[1:] if absolute.anchor else absolute.parts
        for part in parts:
            try:
                next_fd = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, mode, dir_fd=fd)
                except FileExistsError:
                    # Another caller won the create race. The descriptor open
                    # below still verifies the winner is a real directory and
                    # refuses symlinks.
                    pass
                next_fd = os.open(part, flags, dir_fd=fd)
            except OSError as exc:
                if exc.errno in {
                    errno.ELOOP,
                    errno.ENOTDIR,
                    errno.ENXIO,
                    errno.EAGAIN,
                }:
                    raise UnsafePathError(f"Refusing unsafe directory component in {absolute}") from exc
                raise
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def ensure_directory_no_symlinks(
    path: str | os.PathLike[str],
    *,
    mode: int = 0o755,
) -> Path:
    """Create a directory tree without following any symlink component."""
    absolute = _absolute_lexical_path(path)
    fd = open_directory_no_symlinks(absolute, create=True, mode=mode)
    os.close(fd)
    return absolute


def open_file_no_symlinks(
    path: str | os.PathLike[str],
    flags: int,
    mode: int = 0o600,
) -> int:
    """Open one file while pinning every ancestor directory by descriptor."""
    absolute = _absolute_lexical_path(path)
    if not _supports_secure_dir_fd():
        validate_no_symlink_components(absolute.parent)
        final_flags = flags | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
        return os.open(absolute, final_flags, mode)

    parent_fd = open_directory_no_symlinks(absolute.parent)
    try:
        try:
            return os.open(
                absolute.name,
                flags | os.O_NOFOLLOW,
                mode,
                dir_fd=parent_fd,
            )
        except OSError as exc:
            if exc.errno in {
                errno.ELOOP,
                errno.EISDIR,
                errno.ENOTDIR,
                errno.ENXIO,
                errno.EAGAIN,
            }:
                raise UnsafePathError(f"Refusing unsafe file path: {absolute}") from exc
            raise
    finally:
        os.close(parent_fd)


def read_file_no_symlinks(path: str | os.PathLike[str]) -> bytes:
    """Read an exclusive regular file without following symlink components."""
    flags = os.O_RDONLY | (os.O_NONBLOCK if hasattr(os, "O_NONBLOCK") else 0)
    fd = open_file_no_symlinks(path, flags)
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
            raise UnsafePathError(f"Path is not an exclusive regular file: {path}")
        with os.fdopen(fd, "rb") as file:
            fd = -1
            return file.read()
    finally:
        if fd >= 0:
            os.close(fd)


def _default_local_base_dir() -> Path:
    """Return the caller project's writable DeerFlow state directory."""
    return runtime_home()


def validate_thread_id(thread_id: str) -> str:
    """Validate a thread ID before using it in filesystem paths."""
    if not isinstance(thread_id, str) or _SAFE_THREAD_ID_RE.fullmatch(thread_id) is None:
        raise ValueError(f"Invalid thread_id {thread_id!r}: only alphanumeric characters, hyphens, and underscores are allowed.")
    if len(thread_id.encode("utf-8")) > _MAX_PATH_COMPONENT_UTF8_BYTES:
        raise ValueError(f"Invalid thread_id {thread_id!r}: maximum length is {_MAX_PATH_COMPONENT_UTF8_BYTES} UTF-8 bytes.")
    return thread_id


def validate_user_id(user_id: str) -> str:
    """Validate a user ID before using it in filesystem paths."""
    if not isinstance(user_id, str) or _SAFE_USER_ID_RE.fullmatch(user_id) is None:
        raise ValueError(f"Invalid user_id {user_id!r}: only alphanumeric characters, hyphens, and underscores are allowed.")
    if len(user_id.encode("utf-8")) > _MAX_PATH_COMPONENT_UTF8_BYTES:
        raise ValueError(f"Invalid user_id {user_id!r}: maximum length is {_MAX_PATH_COMPONENT_UTF8_BYTES} UTF-8 bytes.")
    return user_id


# Private aliases preserve older imports while API and persistence layers share
# one full-string validation contract.
_validate_thread_id = validate_thread_id
_validate_user_id = validate_user_id


def make_safe_user_id(raw: str) -> str:
    """Normalize an external identity into the user-id charset (``[A-Za-z0-9_-]``).

    IM channel ids (Feishu/Slack/Telegram) may contain characters that
    :func:`_validate_user_id` rejects. Already-safe ids pass through unchanged;
    lossy ones get a short digest suffix so two distinct inputs never share a
    storage bucket.
    """
    if not raw:
        raise ValueError("user_id must be a non-empty string.")
    sanitized = _UNSAFE_USER_ID_CHAR_RE.sub("-", raw)
    if sanitized == raw and len(raw.encode("utf-8")) <= _MAX_PATH_COMPONENT_UTF8_BYTES:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:_SAFE_USER_ID_DIGEST_HEX_LEN]
    suffix = f"-{digest}"
    safe_user_id = f"{sanitized[: _MAX_PATH_COMPONENT_UTF8_BYTES - len(suffix)]}{suffix}"
    return validate_user_id(safe_user_id)


def _legacy_safe_user_id(raw: str, sanitized: str) -> str:
    """Bucket name produced by the previous (SHA-1) digest revision for ``raw``."""
    digest = hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:_SAFE_USER_ID_DIGEST_HEX_LEN]
    suffix = f"-{digest}"
    return f"{sanitized[: _MAX_PATH_COMPONENT_UTF8_BYTES - len(suffix)]}{suffix}"


def _join_host_path(base: str, *parts: str) -> str:
    """Join host filesystem path segments while preserving native style.

    Docker Desktop on Windows expects bind mount sources to stay in Windows
    path form (for example ``C:\\repo\\backend\\.deer-flow``).  Using
    ``Path(base) / ...`` on a POSIX host can accidentally rewrite those paths
    with mixed separators, so this helper preserves the original style.
    """
    if not parts:
        return base

    if re.match(r"^[A-Za-z]:[\\/]", base) or base.startswith("\\\\") or "\\" in base:
        result = PureWindowsPath(base)
        for part in parts:
            result /= part
        return str(result)

    result = Path(base)
    for part in parts:
        result /= part
    return str(result)


def join_host_path(base: str, *parts: str) -> str:
    """Join host filesystem path segments while preserving native style."""
    return _join_host_path(base, *parts)


class Paths:
    """
    Centralized path configuration for DeerFlow application data.

    Directory layout (host side):
        {base_dir}/
        ├── memory.json
        ├── USER.md          <-- global user profile (injected into all agents)
        ├── agents/
        │   └── {agent_name}/
        │       ├── config.yaml
        │       ├── SOUL.md  <-- agent personality/identity (injected alongside lead prompt)
        │       └── memory.json
        └── threads/
            └── {thread_id}/
                └── user-data/         <-- mounted as /mnt/user-data/ inside sandbox
                    ├── workspace/     <-- /mnt/user-data/workspace/
                    ├── uploads/       <-- /mnt/user-data/uploads/
                    ├── inputs/        <-- /mnt/user-data/inputs/ (Goal Cell sealed copies)
                    └── outputs/       <-- /mnt/user-data/outputs/

    BaseDir resolution (in priority order):
        1. Constructor argument `base_dir`
        2. DEER_FLOW_HOME environment variable
        3. Source checkout fallback: `{project_root}/backend/.deer-flow`
        4. Standalone project fallback: `{project_root}/.deer-flow`
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self._base_dir = Path(base_dir).resolve() if base_dir is not None else None

    @property
    def host_base_dir(self) -> Path:
        """Host-visible base dir for Docker volume mount sources.

        When running inside Docker with a mounted Docker socket (DooD), the Docker
        daemon runs on the host and resolves mount paths against the host filesystem.
        Set DEER_FLOW_HOST_BASE_DIR to the host-side path that corresponds to this
        container's base_dir so that sandbox container volume mounts work correctly.

        Falls back to base_dir when the env var is not set (native/local execution).
        """
        if env := os.getenv("DEER_FLOW_HOST_BASE_DIR"):
            return Path(env)
        return self.base_dir

    def _host_base_dir_str(self) -> str:
        """Return the host base dir as a raw string for bind mounts."""
        if env := os.getenv("DEER_FLOW_HOST_BASE_DIR"):
            return env
        return str(self.base_dir)

    @property
    def base_dir(self) -> Path:
        """Root directory for all application data."""
        if self._base_dir is not None:
            return self._base_dir

        if env_home := os.getenv("DEER_FLOW_HOME"):
            return Path(env_home).resolve()

        return _default_local_base_dir()

    @property
    def memory_file(self) -> Path:
        """Path to the persisted memory file: `{base_dir}/memory.json`."""
        return self.base_dir / "memory.json"

    @property
    def user_md_file(self) -> Path:
        """Path to the global user profile file: `{base_dir}/USER.md`."""
        return self.base_dir / "USER.md"

    @property
    def agents_dir(self) -> Path:
        """Legacy root for shared (pre user-isolation) custom agents: `{base_dir}/agents/`.

        New code should use :meth:`user_agents_dir` instead. This property remains
        only as a read-side fallback for installations that have not yet run the
        ``migrate_user_isolation.py`` script.
        """
        return self.base_dir / "agents"

    def agent_dir(self, name: str) -> Path:
        """Legacy per-agent directory (no user isolation): `{base_dir}/agents/{name}/`."""
        return self.agents_dir / name.lower()

    def agent_memory_file(self, name: str) -> Path:
        """Legacy per-agent memory file: `{base_dir}/agents/{name}/memory.json`."""
        return self.agent_dir(name) / "memory.json"

    def user_dir(self, user_id: str) -> Path:
        """Directory for a specific user: `{base_dir}/users/{user_id}/`."""
        return self.base_dir / "users" / _validate_user_id(user_id)

    def prepare_user_dir_for_raw_id(self, raw_user_id: str) -> str:
        """Return the safe user ID and migrate this ID's legacy unsafe-id bucket.

        A previous branch revision used SHA-1 for unsafe external user IDs.
        New IDs use SHA-256; the legacy bucket name is recomputed from the same
        raw ID, so only this user's own old bucket can ever be moved — a
        different raw ID sharing the sanitized prefix produces a different
        legacy digest and is never touched.
        """
        safe_user_id = make_safe_user_id(raw_user_id)
        sanitized = _UNSAFE_USER_ID_CHAR_RE.sub("-", raw_user_id)
        if safe_user_id == raw_user_id:
            return safe_user_id

        users_dir = self.base_dir / "users"
        target_dir = users_dir / safe_user_id
        legacy_dir = users_dir / _legacy_safe_user_id(raw_user_id, sanitized)
        try:
            if target_dir.exists() or not legacy_dir.is_dir():
                return safe_user_id
            legacy_dir.rename(target_dir)
            logger.info("Migrated legacy unsafe-id user directory to the current digest format")
        except OSError:
            logger.exception("Failed to migrate legacy unsafe-id user directory")
        return safe_user_id

    def user_memory_file(self, user_id: str) -> Path:
        """Per-user memory file: `{base_dir}/users/{user_id}/memory.json`."""
        return self.user_dir(user_id) / "memory.json"

    def user_agents_dir(self, user_id: str) -> Path:
        """Per-user root for that user's custom agents: `{base_dir}/users/{user_id}/agents/`."""
        return self.user_dir(user_id) / "agents"

    def user_agent_dir(self, user_id: str, agent_name: str) -> Path:
        """Per-user per-agent directory: `{base_dir}/users/{user_id}/agents/{name}/`."""
        return self.user_agents_dir(user_id) / agent_name.lower()

    def user_agent_memory_file(self, user_id: str, agent_name: str) -> Path:
        """Per-user per-agent memory: `{base_dir}/users/{user_id}/agents/{name}/memory.json`."""
        return self.user_agent_dir(user_id, agent_name) / "memory.json"

    def user_role_assignments_file(self, user_id: str) -> Path:
        """Per-user professional-role model assignments."""
        return self.user_dir(user_id) / "role-assignments.json"

    def thread_dir(self, thread_id: str, *, user_id: str | None = None) -> Path:
        """
        Host path for a thread's data.

        When *user_id* is provided:
            `{base_dir}/users/{user_id}/threads/{thread_id}/`
        Otherwise (legacy layout):
            `{base_dir}/threads/{thread_id}/`

        This directory contains a `user-data/` subdirectory that is mounted
        as `/mnt/user-data/` inside the sandbox.

        Raises:
            ValueError: If `thread_id` or `user_id` contains unsafe characters (path
                        separators or `..`) that could cause directory traversal.
        """
        if user_id is not None:
            return self.user_dir(user_id) / "threads" / _validate_thread_id(thread_id)
        return self.base_dir / "threads" / _validate_thread_id(thread_id)

    def sandbox_work_dir(self, thread_id: str, *, user_id: str | None = None) -> Path:
        """
        Host path for the agent's workspace directory.
        Host: `{base_dir}/threads/{thread_id}/user-data/workspace/`
        Sandbox: `/mnt/user-data/workspace/`
        """
        return self.thread_dir(thread_id, user_id=user_id) / "user-data" / "workspace"

    def sandbox_uploads_dir(self, thread_id: str, *, user_id: str | None = None) -> Path:
        """
        Host path for user-uploaded files.
        Host: `{base_dir}/threads/{thread_id}/user-data/uploads/`
        Sandbox: `/mnt/user-data/uploads/`
        """
        return self.thread_dir(thread_id, user_id=user_id) / "user-data" / "uploads"

    def sandbox_outputs_dir(self, thread_id: str, *, user_id: str | None = None) -> Path:
        """
        Host path for agent-generated artifacts.
        Host: `{base_dir}/threads/{thread_id}/user-data/outputs/`
        Sandbox: `/mnt/user-data/outputs/`
        """
        return self.thread_dir(thread_id, user_id=user_id) / "user-data" / "outputs"

    def sandbox_inputs_dir(self, thread_id: str, *, user_id: str | None = None) -> Path:
        """Host path for a Goal Cell's sealed input capsule.

        The directory is deliberately not part of :meth:`ensure_thread_dirs`:
        a Goal Cell creator writes exact byte snapshots there and then makes
        the capsule read-only. Ordinary threads do not need an inputs mount.
        """
        return self.thread_dir(thread_id, user_id=user_id) / "user-data" / "inputs"

    def acp_workspace_dir(self, thread_id: str, *, user_id: str | None = None) -> Path:
        """
        Host path for the ACP workspace of a specific thread.
        Host: `{base_dir}/threads/{thread_id}/acp-workspace/`
        Sandbox: `/mnt/acp-workspace/`

        Each thread gets its own isolated ACP workspace so that concurrent
        sessions cannot read each other's ACP agent outputs.
        """
        return self.thread_dir(thread_id, user_id=user_id) / "acp-workspace"

    def sandbox_user_data_dir(self, thread_id: str, *, user_id: str | None = None) -> Path:
        """
        Host path for the user-data root.
        Host: `{base_dir}/threads/{thread_id}/user-data/`
        Sandbox: `/mnt/user-data/`
        """
        return self.thread_dir(thread_id, user_id=user_id) / "user-data"

    def host_thread_dir(self, thread_id: str, *, user_id: str | None = None) -> str:
        """Host path for a thread directory, preserving Windows path syntax."""
        if user_id is not None:
            return _join_host_path(self._host_base_dir_str(), "users", _validate_user_id(user_id), "threads", _validate_thread_id(thread_id))
        return _join_host_path(self._host_base_dir_str(), "threads", _validate_thread_id(thread_id))

    def host_sandbox_user_data_dir(self, thread_id: str, *, user_id: str | None = None) -> str:
        """Host path for a thread's user-data root."""
        return _join_host_path(self.host_thread_dir(thread_id, user_id=user_id), "user-data")

    def host_sandbox_work_dir(self, thread_id: str, *, user_id: str | None = None) -> str:
        """Host path for the workspace mount source."""
        return _join_host_path(self.host_sandbox_user_data_dir(thread_id, user_id=user_id), "workspace")

    def host_sandbox_uploads_dir(self, thread_id: str, *, user_id: str | None = None) -> str:
        """Host path for the uploads mount source."""
        return _join_host_path(self.host_sandbox_user_data_dir(thread_id, user_id=user_id), "uploads")

    def host_sandbox_outputs_dir(self, thread_id: str, *, user_id: str | None = None) -> str:
        """Host path for the outputs mount source."""
        return _join_host_path(self.host_sandbox_user_data_dir(thread_id, user_id=user_id), "outputs")

    def host_acp_workspace_dir(self, thread_id: str, *, user_id: str | None = None) -> str:
        """Host path for the ACP workspace mount source."""
        return _join_host_path(self.host_thread_dir(thread_id, user_id=user_id), "acp-workspace")

    def ensure_thread_dirs(self, thread_id: str, *, user_id: str | None = None) -> None:
        """Create all standard sandbox directories for a thread.

        Directories are created with mode 0o777 so that sandbox containers
        (which may run as a different UID than the host backend process) can
        write to the volume-mounted paths without "Permission denied" errors.
        The explicit chmod() call is necessary because Path.mkdir(mode=...) is
        subject to the process umask and may not yield the intended permissions.

        Includes the ACP workspace directory so it can be volume-mounted into
        the sandbox container at ``/mnt/acp-workspace`` even before the first
        ACP agent invocation.
        """
        for d in [
            self.sandbox_work_dir(thread_id, user_id=user_id),
            self.sandbox_uploads_dir(thread_id, user_id=user_id),
            self.sandbox_outputs_dir(thread_id, user_id=user_id),
            self.acp_workspace_dir(thread_id, user_id=user_id),
        ]:
            fd = open_directory_no_symlinks(d, create=True, mode=0o777)
            try:
                if hasattr(os, "fchmod"):
                    os.fchmod(fd, 0o777)
                else:
                    validate_no_symlink_components(d)
                    d.chmod(0o777)
            finally:
                os.close(fd)

    def delete_thread_dir(self, thread_id: str, *, user_id: str | None = None) -> None:
        """Delete all persisted data for a thread.

        The operation is idempotent: missing thread directories are ignored.
        """
        thread_dir = self.thread_dir(thread_id, user_id=user_id)
        if thread_dir.exists():
            shutil.rmtree(thread_dir)

    def claim_legacy_thread_dirs(
        self,
        thread_id: str,
        owner_user_id: str,
    ) -> int:
        """Move legacy/default thread files into the concrete owner bucket.

        The copy-then-delete path is retryable. Existing conflicting files are
        rejected before mutation so ownership repair never overwrites either
        side silently.
        """
        target = self.thread_dir(thread_id, user_id=owner_user_id)
        sources = [
            self.thread_dir(thread_id),
            self.thread_dir(thread_id, user_id="default"),
        ]

        def assert_tree_has_no_symlinks(candidate: Path) -> None:
            if not os.path.lexists(candidate):
                parent = candidate.parent
                while not os.path.lexists(parent) and parent != parent.parent:
                    parent = parent.parent
                validate_no_symlink_components(parent)
                return
            validate_no_symlink_components(candidate)
            if not candidate.is_dir():
                raise ValueError(f"Refusing non-directory thread root: {candidate}")
            for root, directories, files in os.walk(candidate, followlinks=False):
                root_path = Path(root)
                for name in (*directories, *files):
                    nested = root_path / name
                    if stat.S_ISLNK(os.lstat(nested).st_mode):
                        raise ValueError(f"Refusing symlink inside legacy thread tree: {nested}")

        for candidate in {target, *sources}:
            try:
                assert_tree_has_no_symlinks(candidate)
            except UnsafePathError as exc:
                raise ValueError(str(exc)) from exc

        def assert_compatible(source: Path, destination: Path) -> None:
            if not destination.exists():
                return
            for source_path in source.rglob("*"):
                relative = source_path.relative_to(source)
                destination_path = destination / relative
                if not destination_path.exists() and not destination_path.is_symlink():
                    continue
                if source_path.is_dir() and destination_path.is_dir():
                    continue
                if source_path.is_symlink() and destination_path.is_symlink():
                    if os.readlink(source_path) == os.readlink(destination_path):
                        continue
                elif source_path.is_file() and destination_path.is_file():
                    if source_path.read_bytes() == destination_path.read_bytes():
                        continue
                raise FileExistsError(f"Conflicting thread migration path: {relative}")

        existing_sources = [source for source in sources if source != target and source.exists()]
        for index, source in enumerate(existing_sources):
            assert_compatible(source, target)
            for previous_source in existing_sources[:index]:
                assert_compatible(source, previous_source)
        for source in existing_sources:
            ensure_directory_no_symlinks(target.parent)
            if not target.exists():
                source.rename(target)
                assert_tree_has_no_symlinks(target)
                continue
            shutil.copytree(source, target, dirs_exist_ok=True, symlinks=True)
            assert_tree_has_no_symlinks(target)
            shutil.rmtree(source)
        return len(existing_sources)

    def resolve_virtual_path(self, thread_id: str, virtual_path: str, *, user_id: str | None = None) -> Path:
        """Resolve a sandbox virtual path to the actual host filesystem path.

        Args:
            thread_id: The thread ID.
            virtual_path: Virtual path as seen inside the sandbox, e.g.
                          ``/mnt/user-data/outputs/report.pdf``.
                          Leading slashes are stripped before matching.
            user_id: Optional user ID for user-scoped path resolution.

        Returns:
            The resolved absolute host filesystem path.

        Raises:
            ValueError: If the path does not start with the expected virtual
                        prefix or a path-traversal attempt is detected.
        """
        stripped = virtual_path.lstrip("/")
        prefix = VIRTUAL_PATH_PREFIX.lstrip("/")

        # Require an exact segment-boundary match to avoid prefix confusion
        # (e.g. reject paths like "mnt/user-dataX/...").
        if stripped != prefix and not stripped.startswith(prefix + "/"):
            raise ValueError(f"Path must start with /{prefix}")

        relative = stripped[len(prefix) :].lstrip("/")
        relative_parts = Path(relative).parts
        if any(part == ".." for part in relative_parts):
            raise ValueError("Access denied: path traversal detected")
        base = self.sandbox_user_data_dir(thread_id, user_id=user_id)
        actual = base.joinpath(*relative_parts)

        try:
            actual.relative_to(base)
        except ValueError:
            raise ValueError("Access denied: path traversal detected")

        validate_no_symlink_components(actual, allow_missing=True)
        return actual


# ── Singleton ────────────────────────────────────────────────────────────

_paths: Paths | None = None


def get_paths() -> Paths:
    """Return the global Paths singleton (lazy-initialized)."""
    global _paths
    if _paths is None:
        _paths = Paths()
    return _paths


def resolve_path(path: str) -> Path:
    """Resolve *path* to an absolute ``Path``.

    Relative paths are resolved relative to the application base directory.
    Absolute paths are returned as-is (after normalisation).
    """
    p = Path(path)
    if not p.is_absolute():
        p = get_paths().base_dir / path
    return p.resolve()
