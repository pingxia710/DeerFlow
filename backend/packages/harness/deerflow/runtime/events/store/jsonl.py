"""JSONL file-backed RunEventStore implementation.

Each run's events are stored in a single file under DeerFlow's runtime
home. Owner-scoped writes use:
``{base_dir}/users/{user_id}/threads/{thread_id}/runs/{run_id}.jsonl``
Legacy ownerless writes still use:
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
``list_events()`` reads only the matching run file(s).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from deerflow.runtime.events.store.base import RunEventStore, ThreadTimelinePage
from deerflow.runtime.user_context import AUTO, DEFAULT_USER_ID, get_current_user, resolve_user_id
from deerflow.utils.cancellation import await_task_through_repeated_cancellation

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

    def _thread_dir(self, thread_id: str, *, user_id: str | None = None) -> Path:
        self._validate_id(thread_id, "thread_id")
        if user_id is not None:
            self._validate_id(user_id, "user_id")
            return self._base_dir / "users" / user_id / "threads" / thread_id / "runs"
        return self._base_dir / "threads" / thread_id / "runs"

    def _thread_dirs(self, thread_id: str, *, user_id: str | None = None) -> list[Path]:
        if user_id is not None:
            return [self._thread_dir(thread_id, user_id=user_id), self._thread_dir(thread_id)]

        dirs = [self._thread_dir(thread_id)]
        users_dir = self._base_dir / "users"
        if users_dir.exists():
            for user_dir in sorted(path for path in users_dir.iterdir() if path.is_dir()):
                try:
                    dirs.append(self._thread_dir(thread_id, user_id=user_dir.name))
                except ValueError:
                    logger.debug("Skipping invalid user directory under JSONL store: %s", user_dir)
        return dirs

    def _run_file(self, thread_id: str, run_id: str, *, user_id: str | None = None) -> Path:
        self._validate_id(run_id, "run_id")
        return self._thread_dir(thread_id, user_id=user_id) / f"{run_id}.jsonl"

    def _run_files(self, thread_id: str, run_id: str, *, user_id: str | None = None) -> list[Path]:
        self._validate_id(run_id, "run_id")
        return [thread_dir / f"{run_id}.jsonl" for thread_dir in self._thread_dirs(thread_id, user_id=user_id)]

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
        for thread_dir in self._thread_dirs(thread_id):
            if not thread_dir.exists():
                continue
            for f in thread_dir.glob("*.jsonl"):
                for line in f.read_bytes().splitlines():
                    try:
                        record = json.loads(line)
                        max_seq = max(max_seq, record.get("seq", 0))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        logger.debug("Skipping malformed JSONL line in %s", f)
        return max_seq

    async def _ensure_seq_loaded(self, thread_id: str) -> None:
        """Load max seq from existing files into the in-memory counter (non-blocking)."""
        if thread_id in self._seq_counters:
            return
        max_seq = await asyncio.to_thread(self._compute_max_seq, thread_id)
        self._seq_counters[thread_id] = max_seq

    @staticmethod
    async def _run_blocking_mutation(function, *args):
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await await_task_through_repeated_cancellation(task)
            raise

    def _write_record(self, record: dict) -> None:
        path = self._run_file(record["thread_id"], record["run_id"], user_id=record.get("user_id"))
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(record, default=str, ensure_ascii=False) + "\n").encode("utf-8")
        with path.open("a+b") as event_file:
            event_file.seek(0, os.SEEK_END)
            if event_file.tell() > 0:
                event_file.seek(-1, os.SEEK_END)
                if event_file.read(1) != b"\n":
                    event_file.write(b"\n")
            event_file.write(payload)

    @staticmethod
    def _rollback_record_writes(original_sizes: dict[Path, int | None]) -> None:
        for path, original_size in original_sizes.items():
            if original_size is None:
                path.unlink(missing_ok=True)
            else:
                with path.open("r+b") as event_file:
                    event_file.truncate(original_size)

    def _write_records_atomically(self, records: list[dict]) -> dict[Path, int | None]:
        original_sizes: dict[Path, int | None] = {}
        try:
            for record in records:
                path = self._run_file(record["thread_id"], record["run_id"], user_id=record.get("user_id"))
                original_sizes.setdefault(path, path.stat().st_size if path.exists() else None)
                self._write_record(record)
        except BaseException:
            try:
                self._rollback_record_writes(original_sizes)
            except OSError as rollback_exc:
                raise RuntimeError("Failed to roll back partial JSONL event batch") from rollback_exc
            raise
        return original_sizes

    @staticmethod
    def _read_events_file(path: Path) -> list[dict]:
        events = []
        if not path.exists():
            return events
        for line in path.read_bytes().splitlines():
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except (json.JSONDecodeError, UnicodeDecodeError):
                logger.debug("Skipping malformed JSONL line in %s", path)
        return events

    @staticmethod
    def _rewrite_events_file(path: Path, events: list[dict]) -> None:
        if not events:
            # Unlink is atomic: a failure leaves the existing file intact, while
            # a successful removal is already the requested empty projection.
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            # Build and fsync the replacement beside the destination.  A write,
            # serialization, or replace failure therefore cannot truncate the
            # currently durable JSONL file, and claim retries can start from the
            # exact pre-attempt contents.
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                for event in events:
                    temp_file.write(json.dumps(event, default=str, ensure_ascii=False) + "\n")
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, path)
            temp_path = None
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning(
                        "Could not remove failed JSONL rewrite temp file %s",
                        temp_path,
                        exc_info=True,
                    )

    def _read_thread_events(self, thread_id: str, user_id: str | None = None) -> list[dict]:
        """Read all events for a thread, sorted by seq (blocking I/O)."""
        events = []
        for thread_dir in self._thread_dirs(thread_id, user_id=user_id):
            if not thread_dir.exists():
                continue
            for f in sorted(thread_dir.glob("*.jsonl")):
                events.extend(self._read_events_file(f))
        events.sort(key=lambda e: e.get("seq", 0))
        return events

    def _thread_has_events(self, thread_id: str, user_id: str | None = None) -> bool:
        """Short-circuit on the first matching event without loading full files."""
        for thread_dir in self._thread_dirs(thread_id, user_id=user_id):
            if not thread_dir.exists():
                continue
            for path in sorted(thread_dir.glob("*.jsonl")):
                with path.open("rb") as event_file:
                    for line in event_file:
                        if not line.strip():
                            continue
                        try:
                            event = json.loads(line)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            logger.debug("Skipping malformed JSONL line in %s", path)
                            continue
                        if self._matches_user(event, user_id):
                            return True
        return False

    def _read_run_events(self, thread_id: str, run_id: str, user_id: str | None = None) -> list[dict]:
        """Read events for a specific run file (blocking I/O)."""
        events = []
        for path in self._run_files(thread_id, run_id, user_id=user_id):
            events.extend(self._read_events_file(path))
        events.sort(key=lambda e: e.get("seq", 0))
        return events

    def _delete_thread_files(self, thread_id: str) -> None:
        for thread_dir in self._thread_dirs(thread_id):
            if not thread_dir.exists():
                continue
            for f in thread_dir.glob("*.jsonl"):
                f.unlink()

    def _delete_thread_events_for_user(self, thread_id: str, user_id: str) -> int:
        count = 0
        for thread_dir in self._thread_dirs(thread_id, user_id=user_id):
            if not thread_dir.exists():
                continue
            for f in thread_dir.glob("*.jsonl"):
                events = self._read_events_file(f)
                remaining = []
                for event in events:
                    if self._matches_user(event, user_id):
                        count += 1
                    else:
                        remaining.append(event)
                self._rewrite_events_file(f, remaining)
        return count

    def _delete_legacy_thread_events(self, thread_id: str) -> int:
        count = 0
        thread_dir = self._thread_dir(thread_id)
        if not thread_dir.exists():
            return count
        for path in thread_dir.glob("*.jsonl"):
            events = self._read_events_file(path)
            remaining = []
            for event in events:
                if event.get("user_id") is None:
                    count += 1
                else:
                    remaining.append(event)
            self._rewrite_events_file(path, remaining)
        return count

    def _claim_legacy_thread_events(
        self,
        thread_id: str,
        owner_user_id: str,
    ) -> int:
        claimed = 0
        target_dir = self._thread_dir(thread_id, user_id=owner_user_id)
        target_events: dict[Path, list[dict]] = {}
        target_files_to_write: set[Path] = set()
        source_rewrites: list[tuple[Path, list[dict]]] = []
        event_keys_by_seq: dict[object, str] = {}

        if target_dir.exists():
            for target_file in sorted(target_dir.glob("*.jsonl")):
                events = self._read_events_file(target_file)
                deduped: list[dict] = []
                for event in events:
                    seq = event.get("seq")
                    key = json.dumps(event, sort_keys=True, default=str)
                    existing_key = event_keys_by_seq.get(seq)
                    if existing_key is not None:
                        if existing_key != key:
                            raise ValueError(f"Conflicting event seq {seq} while claiming thread {thread_id}")
                        target_files_to_write.add(target_file)
                        continue
                    event_keys_by_seq[seq] = key
                    deduped.append(event)
                target_events[target_file] = deduped

        source_dirs = [
            self._thread_dir(thread_id),
            self._thread_dir(thread_id, user_id=DEFAULT_USER_ID),
        ]
        for source_dir in (source for source in source_dirs if source != target_dir):
            if not source_dir.exists():
                continue
            for source_file in sorted(source_dir.glob("*.jsonl")):
                source_events = self._read_events_file(source_file)
                remaining: list[dict] = []
                moving: list[dict] = []
                for event in source_events:
                    if event.get("user_id") in {None, DEFAULT_USER_ID}:
                        moving.append({**event, "user_id": owner_user_id})
                    else:
                        remaining.append(event)
                if not moving:
                    continue
                source_rewrites.append((source_file, remaining))
                target_file = target_dir / source_file.name
                existing = target_events.setdefault(target_file, self._read_events_file(target_file))
                for event in moving:
                    key = json.dumps(event, sort_keys=True, default=str)
                    seq = event.get("seq")
                    existing_key = event_keys_by_seq.get(seq)
                    if existing_key is not None:
                        if existing_key != key:
                            raise ValueError(f"Conflicting event seq {seq} while claiming thread {thread_id}")
                        continue
                    existing.append(event)
                    event_keys_by_seq[seq] = key
                    target_files_to_write.add(target_file)
                    claimed += 1

        # Conflict detection above is a full preflight: no file is changed
        # until every source and the owner target agree on each cursor seq.
        for target_file in sorted(target_files_to_write):
            existing = target_events[target_file]
            existing.sort(key=lambda event: event.get("seq", 0))
            self._rewrite_events_file(target_file, existing)
        for source_file, remaining in source_rewrites:
            self._rewrite_events_file(source_file, remaining)
        return claimed

    def _delete_run_events_for_user(self, thread_id: str, run_id: str, user_id: str) -> int:
        count = 0
        for path in self._run_files(thread_id, run_id, user_id=user_id):
            events = self._read_events_file(path)
            remaining = []
            for event in events:
                if self._matches_user(event, user_id):
                    count += 1
                else:
                    remaining.append(event)
            self._rewrite_events_file(path, remaining)
        return count

    def _delete_run_file(self, thread_id: str, run_id: str) -> None:
        for path in self._run_files(thread_id, run_id):
            if path.exists():
                path.unlink()

    async def put(self, *, thread_id, run_id, event_type, category, content="", metadata=None, created_at=None, user_id=None):
        async with self._get_write_lock(thread_id):
            await self._ensure_seq_loaded(thread_id)
            initial_seq = self._seq_counters[thread_id]
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
            try:
                write_task = asyncio.create_task(asyncio.to_thread(self._write_records_atomically, [record]))
                original_sizes = await asyncio.shield(write_task)
            except asyncio.CancelledError:
                try:
                    original_sizes = await await_task_through_repeated_cancellation(write_task)
                    await self._run_blocking_mutation(self._rollback_record_writes, original_sizes)
                finally:
                    self._seq_counters[thread_id] = initial_seq
                raise
            except Exception:
                self._seq_counters[thread_id] = initial_seq
                raise
            return record

    async def put_batch(self, events):
        if not events:
            return []
        thread_ids = {event["thread_id"] for event in events}
        if len(thread_ids) != 1:
            raise ValueError(f"put_batch requires all events to belong to the same thread; got {thread_ids!r}")
        thread_id = events[0]["thread_id"]
        async with self._get_write_lock(thread_id):
            await self._ensure_seq_loaded(thread_id)
            initial_seq = self._seq_counters[thread_id]
            records: list[dict] = []
            for event in events:
                record = {
                    "thread_id": thread_id,
                    "run_id": event["run_id"],
                    "event_type": event["event_type"],
                    "category": event.get("category", "trace"),
                    "content": event.get("content", ""),
                    "metadata": event.get("metadata") or {},
                    "seq": self._next_seq(thread_id),
                    "created_at": event.get("created_at") or datetime.now(UTC).isoformat(),
                }
                resolved_user_id = event.get("user_id")
                if resolved_user_id is None:
                    resolved_user_id = self._user_id_from_context()
                if resolved_user_id is not None:
                    record["user_id"] = str(resolved_user_id)
                records.append(record)
            try:
                write_task = asyncio.create_task(asyncio.to_thread(self._write_records_atomically, records))
                original_sizes = await asyncio.shield(write_task)
            except asyncio.CancelledError:
                try:
                    original_sizes = await await_task_through_repeated_cancellation(write_task)
                    await self._run_blocking_mutation(self._rollback_record_writes, original_sizes)
                finally:
                    self._seq_counters[thread_id] = initial_seq
                raise
            except Exception:
                self._seq_counters[thread_id] = initial_seq
                raise
            return records

    async def list_messages(self, thread_id, *, limit=50, before_seq=None, after_seq=None, user_id=None):
        resolved_user_id = self._resolve_filter_user_id(user_id, method_name="JsonlRunEventStore.list_messages")
        all_events = await asyncio.to_thread(self._read_thread_events, thread_id, resolved_user_id)
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
        events = await asyncio.to_thread(self._read_run_events, thread_id, run_id, resolved_user_id)
        events = self._filter_user(events, resolved_user_id)
        if event_types is not None:
            events = [e for e in events if e.get("event_type") in event_types]
        if after_seq is not None:
            events = [e for e in events if e.get("seq", 0) > after_seq]
        return events[:limit]

    async def list_messages_by_run(self, thread_id, run_id, *, limit=50, before_seq=None, after_seq=None, user_id=None):
        resolved_user_id = self._resolve_filter_user_id(user_id, method_name="JsonlRunEventStore.list_messages_by_run")
        events = await asyncio.to_thread(self._read_run_events, thread_id, run_id, resolved_user_id)
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

    async def read_thread_timeline(
        self,
        thread_id: str,
        *,
        categories: set[str],
        limit: int = 100,
        after_seq: int | None = None,
        user_id: str | None = None,
    ) -> ThreadTimelinePage:
        resolved_user_id = self._resolve_filter_user_id(user_id, method_name="JsonlRunEventStore.read_thread_timeline")
        async with self._get_write_lock(thread_id):
            events = await asyncio.to_thread(self._read_thread_events, thread_id, resolved_user_id)
        events = self._filter_user(events, resolved_user_id)
        timeline = [event for event in events if event.get("category") in categories]
        watermark_seq = max((int(event.get("seq", 0)) for event in timeline), default=0)
        if after_seq is not None:
            available = [event for event in timeline if after_seq < event.get("seq", 0) <= watermark_seq]
            records = available[:limit]
            return ThreadTimelinePage(
                records=records,
                watermark_seq=watermark_seq,
                has_more=len(available) > len(records),
            )

        records = timeline[-limit:]
        return ThreadTimelinePage(
            records=records,
            watermark_seq=watermark_seq,
            truncated=len(timeline) > len(records),
        )

    async def count_messages(self, thread_id, *, user_id=None):
        resolved_user_id = self._resolve_filter_user_id(user_id, method_name="JsonlRunEventStore.count_messages")
        all_events = await asyncio.to_thread(self._read_thread_events, thread_id, resolved_user_id)
        all_events = self._filter_user(all_events, resolved_user_id)
        return sum(1 for e in all_events if e.get("category") == "message")

    async def has_events(self, thread_id, *, user_id=None):
        resolved_user_id = self._resolve_filter_user_id(user_id, method_name="JsonlRunEventStore.has_events")
        return await asyncio.to_thread(
            self._thread_has_events,
            thread_id,
            resolved_user_id,
        )

    async def delete_by_thread(self, thread_id, *, user_id=None):
        resolved_user_id = self._resolve_filter_user_id(user_id, method_name="JsonlRunEventStore.delete_by_thread")
        async with self._get_write_lock(thread_id):
            all_events = await asyncio.to_thread(self._read_thread_events, thread_id, resolved_user_id)
            try:
                if resolved_user_id is None:
                    count = len(all_events)
                    await self._run_blocking_mutation(self._delete_thread_files, thread_id)
                else:
                    count = sum(1 for event in all_events if self._matches_user(event, resolved_user_id))
                    await self._run_blocking_mutation(self._delete_thread_events_for_user, thread_id, resolved_user_id)
            finally:
                self._seq_counters.pop(thread_id, None)
            # ponytail: retain per-thread locks for the process lifetime; use a
            # ref-counted lock registry only if thread-id churn becomes material.
            return count

    async def delete_by_run(self, thread_id, run_id, *, user_id=None):
        resolved_user_id = self._resolve_filter_user_id(user_id, method_name="JsonlRunEventStore.delete_by_run")
        async with self._get_write_lock(thread_id):
            events = await asyncio.to_thread(self._read_run_events, thread_id, run_id, resolved_user_id)
            if resolved_user_id is None:
                count = len(events)
                await self._run_blocking_mutation(self._delete_run_file, thread_id, run_id)
            else:
                count = sum(1 for event in events if self._matches_user(event, resolved_user_id))
                await self._run_blocking_mutation(self._delete_run_events_for_user, thread_id, run_id, resolved_user_id)
            return count

    async def delete_legacy_by_thread(self, thread_id):
        async with self._get_write_lock(thread_id):
            try:
                count = await self._run_blocking_mutation(self._delete_legacy_thread_events, thread_id)
            finally:
                self._seq_counters.pop(thread_id, None)
            return count

    async def claim_legacy_by_thread(
        self,
        thread_id: str,
        owner_user_id: str,
    ) -> int:
        self._validate_id(owner_user_id, "owner_user_id")
        async with self._get_write_lock(thread_id):
            try:
                count = await self._run_blocking_mutation(
                    self._claim_legacy_thread_events,
                    thread_id,
                    owner_user_id,
                )
            finally:
                self._seq_counters.pop(thread_id, None)
            return count

    async def list_owners_by_thread(self, thread_id: str) -> set[str | None]:
        events = await asyncio.to_thread(self._read_thread_events, thread_id, None)
        return {event.get("user_id") for event in events}
