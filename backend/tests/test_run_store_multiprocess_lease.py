from __future__ import annotations

import asyncio
import os
import queue
from multiprocessing import get_context
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import deerflow.persistence.models  # noqa: F401
from deerflow.persistence.base import Base
from deerflow.persistence.run import RunRepository
from deerflow.persistence.run.model import RunRow


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        return {"connect_args": {"timeout": 30}}
    return {}


async def _repo_for_url(url: str) -> tuple[object, RunRepository]:
    engine = create_async_engine(url, **_engine_kwargs(url))
    return engine, RunRepository(async_sessionmaker(engine, expire_on_commit=False))


async def _seed_pending_runs(url: str, thread_id: str, run_ids: list[str]) -> None:
    engine, repo = await _repo_for_url(url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        for run_id in run_ids:
            await repo.put(run_id, thread_id=thread_id, status="pending", user_id="lease-test-owner")
    finally:
        await engine.dispose()


async def _cleanup_runs(url: str, run_ids: list[str]) -> None:
    engine = create_async_engine(url, **_engine_kwargs(url))
    try:
        async with engine.begin() as conn:
            await conn.execute(delete(RunRow).where(RunRow.run_id.in_(run_ids)))
    finally:
        await engine.dispose()


async def _acquire_in_child(url: str, thread_id: str, run_id: str) -> dict:
    engine, repo = await _repo_for_url(url)
    try:
        lease = await repo.try_acquire_active_slot(thread_id, run_id, owner_worker_id=f"worker-{run_id}")
        row = await repo.get(run_id, user_id=None)
        return {
            "run_id": run_id,
            "acquired": lease is not None,
            "status": row["status"] if row else None,
            "generation": lease.generation if lease else None,
        }
    finally:
        await engine.dispose()


def _lease_worker(url: str, thread_id: str, run_id: str, result_queue) -> None:
    try:
        result_queue.put(asyncio.run(_acquire_in_child(url, thread_id, run_id)))
    except BaseException as exc:  # pragma: no cover - surfaced through process exit
        result_queue.put({"run_id": run_id, "error": repr(exc)})
        raise


def _assert_one_cross_process_winner(url: str, thread_id: str, run_ids: list[str]) -> None:
    ctx = get_context("spawn")
    result_queue = ctx.Queue()
    processes = [ctx.Process(target=_lease_worker, args=(url, thread_id, run_id, result_queue)) for run_id in run_ids]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
    for process in processes:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
            pytest.fail(f"lease worker did not exit: pid={process.pid}")
        assert process.exitcode == 0

    results = []
    for _ in processes:
        try:
            results.append(result_queue.get(timeout=2))
        except queue.Empty as exc:
            raise AssertionError("lease worker produced no result") from exc

    errors = [result for result in results if "error" in result]
    assert errors == []
    winners = [result for result in results if result["acquired"]]
    assert len(winners) == 1
    assert winners[0]["status"] == "running"
    losers = [result for result in results if not result["acquired"]]
    assert {result["status"] for result in losers} == {"pending"}


@pytest.mark.anyio
async def test_sqlite_active_slot_allows_one_winner_across_processes(tmp_path: Path) -> None:
    thread_id = f"thread-{uuid4().hex}"
    run_ids = [f"run-{uuid4().hex}-{idx}" for idx in range(4)]
    url = f"sqlite+aiosqlite:///{(tmp_path / 'lease.db').as_posix()}"

    await _seed_pending_runs(url, thread_id, run_ids)
    try:
        _assert_one_cross_process_winner(url, thread_id, run_ids)
    finally:
        await _cleanup_runs(url, run_ids)


@pytest.mark.anyio
async def test_postgres_active_slot_allows_one_winner_across_processes() -> None:
    url = os.getenv("DEER_FLOW_TEST_RUN_LEASE_POSTGRES_URL")
    if not url:
        pytest.skip("set DEER_FLOW_TEST_RUN_LEASE_POSTGRES_URL to run the Postgres cross-process lease check")

    thread_id = f"thread-{uuid4().hex}"
    run_ids = [f"run-{uuid4().hex}-{idx}" for idx in range(4)]
    await _seed_pending_runs(url, thread_id, run_ids)
    try:
        _assert_one_cross_process_winner(url, thread_id, run_ids)
    finally:
        await _cleanup_runs(url, run_ids)
