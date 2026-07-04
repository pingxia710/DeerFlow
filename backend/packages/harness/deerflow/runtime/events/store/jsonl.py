"""JSONL file-backed RunEventStore implementation.

Each run's events are stored in a single file under DeerFlow's runtime
home:
``{base_dir}/threads/{thread_id}/runs/{run_id}.jsonl``

All categories (message, trace, lifecycle) are in the same file.
This backend is suitable for lightweight single-node deployments.

**Single-process guarantee**: the in-memory seq counter is process-local.
Multi-process deployments sharing the same directory will produce duplicate
or non-monotonic seq values. Use ``DbRunEventStore`` for multi-process or
high-concurrency deployments.

File I/O is offloaded to a thread pool via ``asyncio.to_thread`` so the
event loop is never blocked. Per-thread ``asyncio.Lock`` objects serialise
writes within a single process to prevent interleaved JSONL lines.

Known trade-off: ``list_messages()`` must scan all run files for a
thread since messages from multiple runs need unified seq ordering.
``list_events()`` reads only one file -- the fast path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from deerflow.runtime.events.store.base import RunEventStore
from deerflow.runtime.user_context import AUTO, get_current_user, resolve_user_id

logger = logging.getLogger(__name__)

_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+$")


class JsonlRunEventStore(RunEventStore):
    def __init__(self, base_dir: str | Path | None = None):
        if base_dir is None:
            from deerflow.config.paths import get_paths

            base_dir = get_paths().base_dir
        self._base_dir = Path(base_dir)
        self._seq_counters: dict[str, int] = {}  # thread_id -> current max seq
        # Per-thread asyncio.Lock — serialises concurrent writes within one process.
        self._write_locks: dict[str, asyncio.Lock] = {}

    def _get_write_lock(self, thread_id: str) -> asyncio.Lock:
        return self._write_locks.setdefault(thread_id, asyncio.Lock())

    @staticmethod
    def _validate_id(value: str, label: str) -> str:
        """Validate that an ID is safe for use in filesystem paths."""
        if not value or not _SAFE_ID_PATTERN.match(value):
            raise ValueError(f"Invalid {label}: must be alphanumeric/dash/underscore, got {value!r}")
        return value

    def _thread_dir(self, thread_id: str) -> Path:
        self._validate_id(thread_id, "thread_id")
        return self._base_dir / "threads" / thread_id / "runs"

    def _run_file(self, thread_id: str, run_id: str) -> Path:
        self._validate_id(run_id, "run_id")
        return self._thread_dir(thread_id) / f"{run_id}.jsonl"

    @staticmethod
    def _user_id_from_context() -> str | None:
        user = get_current_user()
        return str(user.id) if user is not None else None

    @staticmethod
    def _resolve_filter_user_id(user_id, *, method_name: str) -> str | None:
        if user_id is AUTO:
            return resolve_user_id(user_id, method_name=method_name)
        return str(user_id) if user_id is not None else None

    @staticmethod
    def _matches_user(record: dict, user_id: str | None) -> bool:
        if user_id is None:
            return True
        record_user_id = record.get("user_id")
        # User-scoped operations require an explicit owner match. Legacy
        # rows without user_id stay visible only to internal unfiltered reads.
        return record_user_id is not None and str(record_user_id) == user_id

    @classmethod
    def _filter_user(cls, events: list[dict], user_id: str | None) -> list[dict]:
        if user_id is None:
            return events
        return [event for event in events if cls._matches_user(event, user_id)]

    def _next_seq(self, thread_id: str) -> int:
        self._seq_counters[thread_id] = self._seq_counters.get(thread_id, 0) + 1
        return self._seq_counters[thread_id]

    def _compute_max_seq(self, thread_id: str) -> int:
        """Scan all run files for a thread and return the current max seq (blocking I/O)."""
        max_seq = 0
        thread_dir = self._thread_dir(thread_id)
        if thread_dir.exists():
            for f in thread_dir.glob("*.jsonl"):
                for line in f.read_text(encoding="utf-8").strip().splitlines():
                    try:
                        record = json.loads(line)
                        max_seq = max(max_seq, record.get("seq", 0))
                    except json.JSONDecodeError:
                        logger.debug("Skipping malformed JSONL line in %s", f)
        return max_seq

    async def _ensure_seq_loaded(self, thread_id: str) -> None:
        """Load max seq from existing files into the in-memory counter (non-blocking)."""
        if thread_id in self._seq_counters:
            return
        max_seq = await asyncio.to_thread(self._compute_max_seq, thread_id)
        self._seq_counters[thread_id] = max_seq

    def _write_record(self, record: dict) -> None:
        path = self._run_file(record["thread_id"], record["run_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")

    def _read_thread_events(self, thread_id: str) -> list[dict]:
        """Read all events for a thread, sorted by seq (blocking I/O)."""
        events = []
        thread_dir = self._thread_dir(thread_id)
        if not thread_dir.exists():
            return events
        for f in sorted(thread_dir.glob("*.jsonl")):
            for line in f.read_text(encoding="utf-8").strip().splitlines():
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.debug("Skipping malformed JSONL line in %s", f)
        events.sort(key=lambda e: e.get("seq", 0))
        return events

    def _read_run_events(self, thread_id: str, run_id: str) -> list[dict]:
        """Read events for a specific run file (blocking I/O)."""
        path = self._run_file(thread_id, run_id)
        if not path.exists():
            return []
        events = []
        for line in path.read_text(encoding="utf-8").strip().splitlines():
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                logger.debug("Skipping malformed JSONL line in %s", path)
        events.sort(key=lambda e: e.get("seq", 0))
        return events

    def _rewrite_run_events(self, thread_id: str, run_id: str, events: list[dict]) -> None:
        path = self._run_file(thread_id, run_id)
        if not events:
            if path.exists():
                path.unlink()
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for event in events:
                f.write(json.dumps(event, default=str, ensure_ascii=False) + "\n")

    def _delete_thread_files(self, thread_id: str) -> None:
        thread_dir = self._thread_dir(thread_id)
        if thread_dir.exists():
            for f in thread_dir.glob("*.jsonl"):
                f.unlink()

    def _delete_thread_events_for_user(self, thread_id: str, user_id: str) -> int:
        count = 0
        thread_dir = self._thread_dir(thread_id)
        if not thread_dir.exists():
            return count
        for f in thread_dir.glob("*.jsonl"):
            events = self._read_run_events(thread_id, f.stem)
            remaining = []
            for event in events:
                if self._matches_user(event, user_id):
                    count += 1
                else:
                    remaining.append(event)
            self._rewrite_run_events(thread_id, f.stem, remaining)
        return count

    def _delete_run_file(self, thread_id: str, run_id: str) -> None:
        path = self._run_file(thread_id, run_id)
        if path.exists():
            path.unlink()

    async def put(self, *, thread_id, run_id, event_type, category, content="", metadata=None, created_at=None, user_id=None):
        async with self._get_write_lock(thread_id):
            await self._ensure_seq_loaded(thread_id)
            seq = self._next_seq(thread_id)
            resolved_user_id = str(user_id) if user_id is not None else self._user_id_from_context()
            record = {
                "thread_id": thread_id,
                "run_id": run_id,
                "event_type": event_type,
                "category": category,
                "content": content,
                "metadata": metadata or {},
                "seq": seq,
                "created_at": created_at or datetime.now(UTC).isoformat(),
            }
            if resolved_user_id is not None:
                record["user_id"] = resolved_user_id
            await asyncio.to_thread(self._write_record, record)
            return record

    async def put_batch(self, events):
        if not events:
            return []
        results = []
        for ev in events:
            record = await self.put(**ev)
            results.append(record)
        return results

    async def list_messages(self, thread_id, *, limit=50, before_seq=None, after_seq=None, user_id=None):
        resolved_user_id = self._resolve_filter_user_id(user_id, method_name="JsonlRunEventStore.list_messages")
        all_events = await asyncio.to_thread(self._read_thread_events, thread_id)
        all_events = self._filter_user(all_events, resolved_user_id)
        messages = [e for e in all_events if e.get("category") == "message"]

        if before_seq is not None:
            messages = [e for e in messages if e["seq"] < before_seq]
            return messages[-limit:]
        elif after_seq is not None:
            messages = [e for e in messages if e["seq"] > after_seq]
            return messages[:limit]
        else:
            return messages[-limit:]

    async def list_events(self, thread_id, run_id, *, event_types=None, limit=500, after_seq=None, user_id=None):
        resolved_user_id = self._resolve_filter_user_id(user_id, method_name="JsonlRunEventStore.list_events")
        events = await asyncio.to_thread(self._read_run_events, thread_id, run_id)
        events = self._filter_user(events, resolved_user_id)
        if event_types is not None:
            events = [e for e in events if e.get("event_type") in event_types]
        if after_seq is not None:
            events = [e for e in events if e.get("seq", 0) > after_seq]
        return events[:limit]

    async def list_messages_by_run(self, thread_id, run_id, *, limit=50, before_seq=None, after_seq=None, user_id=None):
        resolved_user_id = self._resolve_filter_user_id(user_id, method_name="JsonlRunEventStore.list_messages_by_run")
        events = await asyncio.to_thread(self._read_run_events, thread_id, run_id)
        events = self._filter_user(events, resolved_user_id)
        filtered = [e for e in events if e.get("category") == "message"]
        if before_seq is not None:
            filtered = [e for e in filtered if e.get("seq", 0) < before_seq]
        if after_seq is not None:
            filtered = [e for e in filtered if e.get("seq", 0) > after_seq]
        if after_seq is not None:
            return filtered[:limit]
        else:
            return filtered[-limit:] if len(filtered) > limit else filtered

    async def count_messages(self, thread_id, *, user_id=None):
        resolved_user_id = self._resolve_filter_user_id(user_id, method_name="JsonlRunEventStore.count_messages")
        all_events = await asyncio.to_thread(self._read_thread_events, thread_id)
        all_events = self._filter_user(all_events, resolved_user_id)
        return sum(1 for e in all_events if e.get("category") == "message")

    async def delete_by_thread(self, thread_id, *, user_id=None):
        resolved_user_id = self._resolve_filter_user_id(user_id, method_name="JsonlRunEventStore.delete_by_thread")
        async with self._get_write_lock(thread_id):
            all_events = await asyncio.to_thread(self._read_thread_events, thread_id)
            if resolved_user_id is None:
                count = len(all_events)
                await asyncio.to_thread(self._delete_thread_files, thread_id)
            else:
                count = sum(1 for event in all_events if self._matches_user(event, resolved_user_id))
                await asyncio.to_thread(self._delete_thread_events_for_user, thread_id, resolved_user_id)
            self._seq_counters.pop(thread_id, None)
            # Pop the lock inside the held scope to minimise the window where a new caller
            # could obtain a fresh lock while a waiting coroutine still holds the old one.
            # Note: coroutines that already acquired a reference to this lock before the
            # delete will still proceed after we release — this is an accepted narrow race.
            self._write_locks.pop(thread_id, None)
            return count

    async def delete_by_run(self, thread_id, run_id, *, user_id=None):
        resolved_user_id = self._resolve_filter_user_id(user_id, method_name="JsonlRunEventStore.delete_by_run")
        async with self._get_write_lock(thread_id):
            events = await asyncio.to_thread(self._read_run_events, thread_id, run_id)
            if resolved_user_id is None:
                count = len(events)
                await asyncio.to_thread(self._delete_run_file, thread_id, run_id)
            else:
                count = sum(1 for event in events if self._matches_user(event, resolved_user_id))
                remaining = [event for event in events if not self._matches_user(event, resolved_user_id)]
                await asyncio.to_thread(self._rewrite_run_events, thread_id, run_id, remaining)
            return count
