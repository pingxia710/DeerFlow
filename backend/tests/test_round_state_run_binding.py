from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import deerflow.persistence.models  # noqa: F401
from deerflow.persistence.base import Base
from deerflow.persistence.round_state import MemoryRoundStateStore, RoundStateRepository


@asynccontextmanager
async def _round_store_for_kind(kind, tmp_path):
    if kind == "memory":
        yield MemoryRoundStateStore()
        return

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'round-state-contract.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield RoundStateRepository(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()


async def _round_events(round_store, round_id):
    if isinstance(round_store, MemoryRoundStateStore):
        return list(round_store.events.get(round_id, []))
    async with round_store._sf() as session:
        from deerflow.persistence.round_state.model import RoundEventRow

        rows = list((await session.scalars(select(RoundEventRow).where(RoundEventRow.round_id == round_id).order_by(RoundEventRow.seq))).all())
        return [
            {
                "round_id": row.round_id,
                "thread_id": row.thread_id,
                "run_id": row.run_id,
                "event_type": row.event_type,
                "content": row.content_json,
            }
            for row in rows
        ]


async def _append_historical_run_attachment(round_store, *, round_id, thread_id, run_id, user_id):
    if isinstance(round_store, MemoryRoundStateStore):
        round_store._append_event(
            round_id,
            thread_id=thread_id,
            run_id=run_id,
            user_id=user_id,
            event_type="run.attached",
            content={"historical_fixture": True},
        )
        return
    async with round_store._sf() as session:
        async with session.begin():
            await round_store._append_event(
                session,
                round_id=round_id,
                thread_id=thread_id,
                run_id=run_id,
                user_id=user_id,
                event_type="run.attached",
                content={"historical_fixture": True},
            )


@pytest.mark.anyio
@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
async def test_same_run_same_scope_rebind_is_idempotent_even_after_closure(store_kind, tmp_path):
    async with _round_store_for_kind(store_kind, tmp_path) as round_store:
        first = await round_store.bind_run(
            thread_id="thread-idempotent",
            run_id="run-retry",
            user_id="owner-a",
            current_intent="initial intent",
        )
        successor = await round_store.bind_run(
            thread_id="thread-idempotent",
            run_id="run-successor",
            user_id="owner-a",
        )
        assert successor["round_id"] == first["round_id"]
        await round_store.set_run_state(
            "run-successor",
            thread_id="thread-idempotent",
            user_id="owner-a",
            round_id=first["round_id"],
            state="closed",
            event_type="round.closed",
        )

        retried = await round_store.bind_run(
            thread_id="thread-idempotent",
            run_id="run-retry",
            user_id="owner-a",
            current_intent="duplicate input must be ignored",
            metadata={"round_id": first["round_id"]},
        )

        assert retried["round_id"] == first["round_id"]
        assert retried["current_run_id"] == "run-successor"
        assert retried["state"] == "closed"
        events = await _round_events(round_store, first["round_id"])
        assert [event["event_type"] for event in events if event["run_id"] == "run-retry" and event["event_type"] == "run.attached"] == ["run.attached"]
        user_inputs = [event for event in events if event["run_id"] == "run-retry" and event["event_type"] == "user.input"]
        assert [event["content"]["current_intent"] for event in user_inputs] == ["initial intent"]


@pytest.mark.anyio
@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
async def test_ambiguous_historical_attachment_fails_closed_for_binding_and_task_events(store_kind, tmp_path):
    async with _round_store_for_kind(store_kind, tmp_path) as round_store:
        first = await round_store.bind_run(thread_id="thread-ambiguous", run_id="run-ambiguous", user_id="owner-a")
        await round_store.set_run_state(
            "run-ambiguous",
            thread_id="thread-ambiguous",
            user_id="owner-a",
            round_id=first["round_id"],
            state="closed",
            event_type="round.closed",
        )
        other = await round_store.bind_run(thread_id="thread-ambiguous", run_id="run-other", user_id="owner-a")
        await _append_historical_run_attachment(
            round_store,
            round_id=other["round_id"],
            thread_id="thread-ambiguous",
            run_id="run-ambiguous",
            user_id="owner-a",
        )

        with pytest.raises(ValueError):
            await round_store.bind_run(thread_id="thread-ambiguous", run_id="run-ambiguous", user_id="owner-a")
        assert (
            await round_store.set_run_state(
                "run-ambiguous",
                thread_id="thread-ambiguous",
                user_id="owner-a",
                round_id=first["round_id"],
                state="closed",
                event_type="round.closed",
            )
            is None
        )
        await round_store.record_task_events(
            [
                {
                    "type": "task_completed",
                    "thread_id": "thread-ambiguous",
                    "run_id": "run-ambiguous",
                    "task_id": "must-not-project",
                    "status": "completed",
                }
            ]
        )
        assert (
            await round_store.list_task_lanes_by_round(
                thread_id="thread-ambiguous",
                round_id=first["round_id"],
                user_id="owner-a",
            )
            == []
        )
        assert (
            await round_store.list_task_lanes_by_round(
                thread_id="thread-ambiguous",
                round_id=other["round_id"],
                user_id="owner-a",
            )
            == []
        )


@pytest.mark.anyio
@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
async def test_same_run_cross_round_thread_or_owner_rebind_is_rejected(store_kind, tmp_path):
    async with _round_store_for_kind(store_kind, tmp_path) as round_store:
        attached = await round_store.bind_run(thread_id="thread-bound", run_id="run-bound", user_id="owner-a")
        await round_store.set_run_state(
            "run-bound",
            thread_id="thread-bound",
            user_id="owner-a",
            round_id=attached["round_id"],
            state="closed",
            event_type="round.closed",
        )
        other_round = await round_store.bind_run(thread_id="thread-bound", run_id="run-other", user_id="owner-a")
        assert other_round["round_id"] != attached["round_id"]

        with pytest.raises(ValueError):
            await round_store.bind_run(thread_id="thread-other", run_id="run-bound", user_id="owner-a")
        with pytest.raises(ValueError):
            await round_store.bind_run(thread_id="thread-bound", run_id="run-bound", user_id="owner-b")
        with pytest.raises(ValueError):
            await round_store.bind_run(
                thread_id="thread-bound",
                run_id="run-bound",
                user_id="owner-a",
                metadata={"round_id": other_round["round_id"]},
            )


@pytest.mark.anyio
@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
async def test_superseded_run_records_lifecycle_without_overwriting_round_aggregation(store_kind, tmp_path):
    async with _round_store_for_kind(store_kind, tmp_path) as round_store:
        first = await round_store.bind_run(thread_id="thread-superseded", run_id="run-old", user_id="owner-a")
        successor = await round_store.bind_run(thread_id="thread-superseded", run_id="run-new", user_id="owner-a")
        assert successor["round_id"] == first["round_id"]
        assert successor["current_run_id"] == "run-new"

        old_result = await round_store.set_run_state(
            "run-old",
            thread_id="thread-superseded",
            user_id="owner-a",
            round_id=first["round_id"],
            state="closed",
            event_type="round.closed",
            next_action="late old-run action",
        )
        assert old_result is not None
        assert old_result["current_run_id"] == "run-new"
        assert old_result["state"] == "open"
        assert old_result["closed_at"] is None
        assert old_result["next_action"] is None

        old_event = (await _round_events(round_store, first["round_id"]))[-1]
        assert old_event["run_id"] == "run-old"
        assert old_event["event_type"] == "round.closed"
        assert old_event["content"]["requested_state"] == "closed"
        assert old_event["content"]["state_applied"] is False
        assert old_event["content"]["superseded_by_run_id"] == "run-new"

        new_result = await round_store.set_run_state(
            "run-new",
            thread_id="thread-superseded",
            user_id="owner-a",
            round_id=first["round_id"],
            state="executing",
            event_type="run.executing",
        )
        assert new_result is not None
        assert new_result["state"] == "executing"
        assert new_result["current_run_id"] == "run-new"
        new_event = (await _round_events(round_store, first["round_id"]))[-1]
        assert new_event["content"]["state_applied"] is True


@pytest.mark.anyio
@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
async def test_set_run_state_rejects_wrong_scope_or_unbound_run(store_kind, tmp_path):
    async with _round_store_for_kind(store_kind, tmp_path) as round_store:
        attached = await round_store.bind_run(thread_id="thread-scope", run_id="run-scope", user_id="owner-a")
        before_events = await _round_events(round_store, attached["round_id"])
        bad_scopes = [
            {"thread_id": "other-thread", "user_id": "owner-a", "round_id": attached["round_id"]},
            {"thread_id": "thread-scope", "user_id": "owner-b", "round_id": attached["round_id"]},
            {"thread_id": "thread-scope", "user_id": "owner-a", "round_id": "other-round"},
        ]
        for scope in bad_scopes:
            assert (
                await round_store.set_run_state(
                    "run-scope",
                    **scope,
                    state="closed",
                    event_type="round.closed",
                )
                is None
            )
        assert (
            await round_store.set_run_state(
                "unbound-run",
                thread_id="thread-scope",
                user_id="owner-a",
                round_id=attached["round_id"],
                state="closed",
                event_type="round.closed",
            )
            is None
        )

        rows = await round_store.list_by_thread("thread-scope", user_id="owner-a")
        assert rows[0]["state"] == "open"
        assert rows[0]["closed_at"] is None
        assert await _round_events(round_store, attached["round_id"]) == before_events


@pytest.mark.anyio
@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
async def test_explicit_round_id_requires_existing_resumable_same_owner_and_thread(store_kind, tmp_path):
    async with _round_store_for_kind(store_kind, tmp_path) as round_store:
        existing = await round_store.bind_run(thread_id="thread-owner", run_id="run-initial", user_id="owner-a")
        resumed = await round_store.bind_run(
            thread_id="thread-owner",
            run_id="run-resumed",
            user_id="owner-a",
            metadata={"round_id": existing["round_id"]},
        )
        assert resumed["round_id"] == existing["round_id"]

        with pytest.raises((LookupError, ValueError)):
            await round_store.bind_run(
                thread_id="other-thread",
                run_id="run-cross-thread",
                user_id="owner-a",
                metadata={"round_id": existing["round_id"]},
            )
        with pytest.raises((LookupError, ValueError)):
            await round_store.bind_run(
                thread_id="thread-owner",
                run_id="run-cross-owner",
                user_id="owner-b",
                metadata={"round_id": existing["round_id"]},
            )
        for value in ("unknown-round", "", None, 42):
            with pytest.raises((LookupError, ValueError)):
                await round_store.bind_run(
                    thread_id="thread-owner",
                    run_id=f"run-invalid-{value!r}",
                    user_id="owner-a",
                    metadata={"round_id": value},
                )

        await round_store.set_run_state(
            "run-resumed",
            thread_id="thread-owner",
            user_id="owner-a",
            round_id=existing["round_id"],
            state="closed",
            event_type="round.closed",
        )
        with pytest.raises(ValueError):
            await round_store.bind_run(
                thread_id="thread-owner",
                run_id="run-terminal",
                user_id="owner-a",
                metadata={"round_id": existing["round_id"]},
            )

        rounds = await round_store.list_by_thread("thread-owner", user_id="owner-a")
        assert [round_["round_id"] for round_ in rounds] == [existing["round_id"]]
        assert rounds[0]["current_run_id"] == "run-resumed"
        assert await round_store.list_by_thread("other-thread", user_id="owner-a") == []


@pytest.mark.anyio
@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
async def test_task_events_use_only_server_attached_run_mapping(store_kind, tmp_path):
    async with _round_store_for_kind(store_kind, tmp_path) as round_store:
        attached = await round_store.bind_run(thread_id="thread-events", run_id="run-attached", user_id="owner-a")
        other = await round_store.bind_run(thread_id="other-thread", run_id="run-other", user_id="owner-a")
        round_count_before = len(await round_store.list_by_thread("thread-events", user_id="owner-a"))

        await round_store.record_task_events(
            [
                {
                    "type": "task_completed",
                    "thread_id": "forged-thread",
                    "run_id": "run-attached",
                    "task_id": "forged-thread-task",
                    "round_id": other["round_id"],
                    "status": "completed",
                },
                {
                    "type": "task_completed",
                    "thread_id": "thread-events",
                    "run_id": "unbound-run",
                    "task_id": "unbound-task",
                    "round_id": other["round_id"],
                    "status": "completed",
                },
                {
                    "type": "task_completed",
                    "thread_id": "thread-events",
                    "run_id": "run-attached",
                    "task_id": "mapped-task",
                    "round_id": other["round_id"],
                    "status": "completed",
                },
            ]
        )

        lanes = await round_store.list_task_lanes_by_round(
            thread_id="thread-events",
            round_id=attached["round_id"],
            user_id="owner-a",
        )
        assert [(lane["run_id"], lane["task_id"], lane["round_id"]) for lane in lanes] == [
            ("run-attached", "mapped-task", attached["round_id"]),
        ]
        assert (
            await round_store.list_task_lanes_by_round(
                thread_id="other-thread",
                round_id=other["round_id"],
                user_id="owner-a",
            )
            == []
        )
        assert len(await round_store.list_by_thread("thread-events", user_id="owner-a")) == round_count_before


@pytest.mark.anyio
@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
async def test_delayed_task_events_follow_their_original_attached_run(store_kind, tmp_path):
    async with _round_store_for_kind(store_kind, tmp_path) as round_store:
        first = await round_store.bind_run(thread_id="thread-delayed", run_id="run-old", user_id="owner-a")
        second = await round_store.bind_run(thread_id="thread-delayed", run_id="run-new", user_id="owner-a")
        assert second["round_id"] == first["round_id"]

        await round_store.record_task_events(
            [
                {
                    "type": "task_completed",
                    "thread_id": "thread-delayed",
                    "run_id": "run-old",
                    "task_id": "late-old-task",
                    "status": "completed",
                }
            ]
        )

        lanes = await round_store.list_task_lanes_by_round(
            thread_id="thread-delayed",
            round_id=first["round_id"],
            user_id="owner-a",
        )
        assert [(lane["run_id"], lane["task_id"]) for lane in lanes] == [("run-old", "late-old-task")]
