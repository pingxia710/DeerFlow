"""Contract tests for the current owner/thread/checkpoint boundary.

These pin today's behavior before any tenant-key migration. They are
deliberately not a target-state design.
"""

from __future__ import annotations

import pytest
from sqlalchemy import UniqueConstraint

from deerflow.persistence.models.run_event import RunEventRow
from deerflow.persistence.thread_meta import (
    ThreadMetaConflictError,
    ThreadMetaRepository,
)
from deerflow.persistence.thread_meta.model import ThreadMetaRow
from deerflow.runtime.events.store.jsonl import JsonlRunEventStore


@pytest.fixture
async def thread_repo(tmp_path):
    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine

    url = f"sqlite+aiosqlite:///{tmp_path / 'owner-contract.db'}"
    await init_engine("sqlite", url=url, sqlite_dir=str(tmp_path))
    try:
        yield ThreadMetaRepository(get_session_factory())
    finally:
        await close_engine()


def test_thread_meta_primary_key_is_global_thread_id() -> None:
    assert [column.name for column in ThreadMetaRow.__table__.primary_key.columns] == ["thread_id"]


def test_run_event_seq_unique_constraint_is_thread_scoped_not_owner_scoped() -> None:
    unique_columns = {tuple(column.name for column in constraint.columns) for constraint in RunEventRow.__table__.constraints if isinstance(constraint, UniqueConstraint)}

    assert ("thread_id", "seq") in unique_columns
    assert ("user_id", "thread_id", "seq") not in unique_columns


def test_jsonl_run_event_path_is_owner_scoped_when_user_id_is_present(tmp_path) -> None:
    store = JsonlRunEventStore(base_dir=tmp_path)

    assert store._run_file("thread-1", "run-1") == tmp_path / "threads" / "thread-1" / "runs" / "run-1.jsonl"
    assert store._run_file("thread-1", "run-1", user_id="user-1") == tmp_path / "users" / "user-1" / "threads" / "thread-1" / "runs" / "run-1.jsonl"


@pytest.mark.anyio
async def test_duplicate_thread_id_conflicts_across_users(thread_repo) -> None:
    await thread_repo.create("same-thread", user_id="user-1")

    with pytest.raises(ThreadMetaConflictError):
        await thread_repo.create("same-thread", user_id="user-2")


@pytest.mark.anyio
async def test_legacy_null_owner_metadata_access_differs_from_filtered_reads(thread_repo) -> None:
    await thread_repo.create("legacy-thread", user_id=None)

    assert await thread_repo.check_access("legacy-thread", "user-1") is True
    assert await thread_repo.check_access("legacy-thread", "user-1", require_existing=True) is False
    assert await thread_repo.get("legacy-thread", user_id="user-1") is None
    assert await thread_repo.search(user_id="user-1") == []
    assert (await thread_repo.get("legacy-thread", user_id=None))["thread_id"] == "legacy-thread"
