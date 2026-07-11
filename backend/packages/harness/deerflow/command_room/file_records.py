"""Thread-safe filesystem primitives for Command Room audit records."""

from __future__ import annotations

import json
import threading
import weakref
from pathlib import Path
from typing import Any

_WRITE_LOCKS: weakref.WeakValueDictionary[str, threading.Lock] = weakref.WeakValueDictionary()
_WRITE_LOCKS_GUARD = threading.Lock()


def _file_lock(path: Path) -> threading.Lock:
    key = str(path.resolve(strict=False))
    with _WRITE_LOCKS_GUARD:
        lock = _WRITE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _WRITE_LOCKS[key] = lock
        return lock


def append_jsonl_record(path: Path, row: dict[str, Any]) -> Path:
    """Append one JSONL row, serializing only writers targeting the same file."""

    payload = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
    with _file_lock(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        separator = ""
        try:
            with path.open("rb") as existing:
                existing.seek(-1, 2)
                if existing.read(1) != b"\n":
                    separator = "\n"
        except (FileNotFoundError, OSError):
            pass
        with path.open("a", encoding="utf-8") as file:
            file.write(separator + payload)
    return path


def read_jsonl_text(path: Path) -> str | None:
    """Read one complete JSONL snapshot, waiting for an in-process append."""

    with _file_lock(path):
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return None


__all__ = ["append_jsonl_record", "read_jsonl_text"]
