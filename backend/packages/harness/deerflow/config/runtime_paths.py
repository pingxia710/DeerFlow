"""Runtime path resolution for standalone harness usage."""

import os
from pathlib import Path


def _is_source_checkout_root(path: Path) -> bool:
    return (path / "backend" / "packages" / "harness" / "deerflow").is_dir() and (path / "frontend").is_dir()


def project_root() -> Path:
    """Return the caller project root for runtime-owned files."""
    if env_root := os.getenv("DEER_FLOW_PROJECT_ROOT"):
        root = Path(env_root).resolve()
        if not root.exists():
            raise ValueError(f"DEER_FLOW_PROJECT_ROOT is set to '{env_root}', but the resolved path '{root}' does not exist.")
        if not root.is_dir():
            raise ValueError(f"DEER_FLOW_PROJECT_ROOT is set to '{env_root}', but the resolved path '{root}' is not a directory.")
        return root
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if _is_source_checkout_root(candidate):
            return candidate
    return cwd


def default_runtime_home(root: Path | None = None) -> Path:
    """Return the default state directory for a project or source checkout."""
    resolved_root = (root or project_root()).resolve()
    source_backend = resolved_root / "backend"
    if _is_source_checkout_root(resolved_root):
        return source_backend / ".deer-flow"
    return resolved_root / ".deer-flow"


def _runtime_dir_has_state(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        return any(path.iterdir())
    except OSError:
        return True


def runtime_home(root: Path | None = None) -> Path:
    """Return the writable DeerFlow state directory."""
    if env_home := os.getenv("DEER_FLOW_HOME"):
        return Path(env_home).resolve()
    resolved_root = (root or project_root()).resolve()
    canonical_home = default_runtime_home(resolved_root)
    legacy_home = resolved_root / ".deer-flow"
    if canonical_home != legacy_home and _runtime_dir_has_state(legacy_home) and not _runtime_dir_has_state(canonical_home):
        return legacy_home
    return canonical_home


def resolve_runtime_path(value: str | os.PathLike[str]) -> Path:
    """Resolve runtime-owned paths under :func:`runtime_home`.

    Existing configs commonly prefix values with ``.deer-flow``. Since
    ``runtime_home`` already names that directory, strip the legacy prefix to
    avoid creating ``.deer-flow/.deer-flow`` when a custom home is configured.
    """
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    if path.parts and path.parts[0] == ".deer-flow":
        path = Path(*path.parts[1:])
    return (runtime_home() / path).resolve()


def resolve_path(value: str | os.PathLike[str], *, base: Path | None = None) -> Path:
    """Resolve absolute paths as-is and relative paths against the project root."""
    path = Path(value)
    if not path.is_absolute():
        path = (base or project_root()) / path
    return path.resolve()


def existing_project_file(names: tuple[str, ...]) -> Path | None:
    """Return the first existing named file under the project root."""
    root = project_root()
    for name in names:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None
