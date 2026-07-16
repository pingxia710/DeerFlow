import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.routers import thread_runs
from deerflow.agents.middlewares.round_context_middleware import format_native_round_context_for_model
from deerflow.persistence.base import Base
from deerflow.persistence.round_state import MemoryRoundStateStore, RoundStateRepository
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal
from deerflow.runtime.runs.manager import RunManager, RunRecord
from deerflow.runtime.runs.schemas import RunStatus
from deerflow.runtime.runs.store.memory import MemoryRunStore
from deerflow.runtime.user_context import get_effective_user_id


async def _assert_explicit_round_binding_is_owner_scoped(round_store):
    created = await round_store.bind_run(
        thread_id="thread-a",
        run_id="run-a",
        user_id="user-a",
        current_intent="start",
    )
    round_id = created["round_id"]

    with pytest.raises(LookupError):
        await round_store.bind_run(
            thread_id="thread-a",
            run_id="run-missing",
            user_id="user-a",
            metadata={"round_id": "missing-round"},
        )
    with pytest.raises(LookupError):
        await round_store.bind_run(
            thread_id="thread-b",
            run_id="run-wrong-thread",
            user_id="user-a",
            metadata={"round_id": round_id},
        )
    with pytest.raises(LookupError):
        await round_store.bind_run(
            thread_id="thread-a",
            run_id="run-wrong-owner",
            user_id="user-b",
            metadata={"round_id": round_id},
        )

    rebound = await round_store.bind_run(
        thread_id="thread-a",
        run_id="run-rebound",
        user_id="user-a",
        metadata={"round_id": round_id},
    )
    assert rebound["round_id"] == round_id

    await round_store.set_run_state(
        "run-rebound",
        thread_id="thread-a",
        user_id="user-a",
        round_id=round_id,
        state="closed",
        event_type="run.completed",
    )
    with pytest.raises(ValueError):
        await round_store.bind_run(
            thread_id="thread-a",
            run_id="run-after-close",
            user_id="user-a",
            metadata={"round_id": round_id},
        )


@pytest.mark.anyio
async def test_memory_explicit_round_binding_is_owner_scoped_and_nonterminal_only():
    await _assert_explicit_round_binding_is_owner_scoped(MemoryRoundStateStore())


@pytest.mark.anyio
async def test_sql_explicit_round_binding_is_owner_scoped_and_nonterminal_only(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'round-binding.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        await _assert_explicit_round_binding_is_owner_scoped(RoundStateRepository(async_sessionmaker(engine, expire_on_commit=False)))
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_sql_round_event_postgres_seq_uses_transaction_advisory_lock():
    from sqlalchemy.dialects import postgresql

    from deerflow.persistence.round_state.sql import RoundStateRepository

    class FakeSession:
        def __init__(self):
            self.dialect = postgresql.dialect()
            self.execute_calls = []
            self.scalar_stmt = None

        def get_bind(self):
            return self

        async def execute(self, stmt, params=None):
            self.execute_calls.append((stmt, params))

        async def scalar(self, stmt):
            self.scalar_stmt = stmt
            return 7

    session = FakeSession()

    max_seq = await RoundStateRepository._max_seq_for_round(session, "round-1")

    assert max_seq == 7
    assert session.execute_calls[0][1] == {"round_id": "round-1"}
    assert "pg_advisory_xact_lock" in str(session.execute_calls[0][0])
    compiled = str(session.scalar_stmt.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" not in compiled


async def _assert_legacy_round_claim_moves_null_and_default(round_store):
    from deerflow.runtime.user_context import DEFAULT_USER_ID

    await round_store.bind_run(
        thread_id="thread-round-claim",
        run_id="run-null",
        user_id=None,
    )
    await round_store.bind_run(
        thread_id="thread-round-claim",
        run_id="run-default",
        user_id=DEFAULT_USER_ID,
    )
    await round_store.bind_run(
        thread_id="thread-round-claim",
        run_id="run-other",
        user_id="other-owner",
    )

    claimed = await round_store.claim_legacy_by_thread(
        "thread-round-claim",
        "new-owner",
    )

    assert claimed == 2
    assert (
        len(
            await round_store.list_by_thread(
                "thread-round-claim",
                user_id="new-owner",
            )
        )
        == 2
    )
    assert (
        len(
            await round_store.list_by_thread(
                "thread-round-claim",
                user_id="other-owner",
            )
        )
        == 1
    )


@pytest.mark.anyio
async def test_memory_legacy_round_claim_moves_null_and_default():
    await _assert_legacy_round_claim_moves_null_and_default(MemoryRoundStateStore())


@pytest.mark.anyio
async def test_sql_legacy_round_claim_moves_null_and_default(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'round-claim.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        await _assert_legacy_round_claim_moves_null_and_default(RoundStateRepository(async_sessionmaker(engine, expire_on_commit=False)))
    finally:
        await engine.dispose()


async def _assert_run_binding_rollback_restores_prior_rounds(round_store):
    original = await round_store.bind_run(
        thread_id="thread-rollback-open",
        run_id="run-original",
        current_intent="original intent",
    )
    await round_store.bind_run(
        thread_id="thread-rollback-open",
        run_id="run-cancelled",
        current_intent="cancelled intent",
    )
    rollback = getattr(round_store, "rollback_run_binding", None)
    assert rollback is not None
    assert await rollback("run-cancelled") is True
    [restored] = await round_store.list_by_thread("thread-rollback-open")
    assert restored["round_id"] == original["round_id"]
    assert restored["current_run_id"] == "run-original"
    assert restored["current_intent"] == "original intent"

    parent = await round_store.bind_run(
        thread_id="thread-rollback-child",
        run_id="run-parent",
        current_intent="parent intent",
    )
    await round_store.set_run_state(
        "run-parent",
        thread_id="thread-rollback-child",
        user_id=None,
        round_id=parent["round_id"],
        state="closed",
        event_type="run.completed",
    )
    child = await round_store.bind_run(
        thread_id="thread-rollback-child",
        run_id="run-child-cancelled",
        current_intent="child intent",
    )
    assert child["round_id"] != parent["round_id"]

    assert await rollback("run-child-cancelled") is True
    [remaining] = await round_store.list_by_thread("thread-rollback-child")
    assert remaining["round_id"] == parent["round_id"]
    assert remaining["current_run_id"] == "run-parent"
    assert remaining["state"] == "closed"
    assert await rollback("run-child-cancelled") is False


@pytest.mark.anyio
async def test_memory_run_binding_rollback_restores_prior_rounds():
    await _assert_run_binding_rollback_restores_prior_rounds(MemoryRoundStateStore())


@pytest.mark.anyio
async def test_sql_run_binding_rollback_restores_prior_rounds(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'round-binding-rollback.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        await _assert_run_binding_rollback_restores_prior_rounds(RoundStateRepository(async_sessionmaker(engine, expire_on_commit=False)))
    finally:
        await engine.dispose()


async def _assert_round_delete_is_owner_scoped(round_store):
    await round_store.bind_run(thread_id="thread-delete", run_id="run-a", user_id="user-a")
    await round_store.bind_run(thread_id="thread-delete", run_id="run-b", user_id="user-b")
    delete_by_thread = getattr(round_store, "delete_by_thread", None)
    assert delete_by_thread is not None

    await delete_by_thread("thread-delete", user_id="user-a")

    assert await round_store.list_by_thread("thread-delete", user_id="user-a") == []
    assert len(await round_store.list_by_thread("thread-delete", user_id="user-b")) == 1


async def _assert_legacy_round_delete_only_removes_unowned_rows(round_store):
    await round_store.bind_run(thread_id="thread-legacy-delete", run_id="run-legacy")
    await round_store.bind_run(
        thread_id="thread-legacy-delete",
        run_id="run-default",
        user_id="default",
    )
    await round_store.bind_run(
        thread_id="thread-legacy-delete",
        run_id="run-foreign",
        user_id="foreign",
    )

    delete_legacy = getattr(round_store, "delete_legacy_by_thread", None)
    assert callable(delete_legacy)
    await delete_legacy("thread-legacy-delete")

    assert await round_store.list_by_thread("thread-legacy-delete", user_id=None) == []
    assert len(await round_store.list_by_thread("thread-legacy-delete", user_id="default")) == 1
    assert len(await round_store.list_by_thread("thread-legacy-delete", user_id="foreign")) == 1


@pytest.mark.anyio
async def test_memory_round_delete_is_owner_scoped():
    await _assert_round_delete_is_owner_scoped(MemoryRoundStateStore())


@pytest.mark.anyio
async def test_sql_round_delete_is_owner_scoped(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'round-delete.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        await _assert_round_delete_is_owner_scoped(RoundStateRepository(async_sessionmaker(engine, expire_on_commit=False)))
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_memory_legacy_round_delete_only_removes_unowned_rows():
    await _assert_legacy_round_delete_only_removes_unowned_rows(MemoryRoundStateStore())


@pytest.mark.anyio
async def test_sql_legacy_round_delete_only_removes_unowned_rows(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'round-legacy-delete.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        await _assert_legacy_round_delete_only_removes_unowned_rows(RoundStateRepository(async_sessionmaker(engine, expire_on_commit=False)))
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_completed_run_round_followup_starts_new_round_without_next_action_authorization():
    round_store = MemoryRoundStateStore()
    manager = RunManager(store=MemoryRunStore(), round_store=round_store, terminal_cleanup_delay=-1)

    first = await manager.create_or_reject(
        "thread-1",
        kwargs={"input": {"messages": [{"role": "user", "content": "全面检查 DeerFlow"}]}},
    )
    await manager.set_status(first.run_id, RunStatus.running)
    await manager.set_status(first.run_id, RunStatus.success, terminal_reason="success")
    await manager.update_run_completion(first.run_id, status="success", last_ai_message="Next: implement native state.")

    second = await manager.create_or_reject(
        "thread-1",
        kwargs={"input": {"messages": [{"role": "user", "content": "好的，下一步"}]}},
    )

    assert first.round_id
    assert second.round_id
    assert second.round_id != first.round_id
    context = second.metadata["round_context"]
    assert context["parent_round_id"] == first.round_id
    assert context["current_intent"] == "好的，下一步"
    assert context["parent_intent"] == "全面检查 DeerFlow"
    assert "accepted_next_action" not in context
    assert "next_action" not in context

    first_round = next(round_ for round_ in await round_store.list_by_thread("thread-1") if round_["round_id"] == first.round_id)
    assert first_round["state"] == "closed"
    assert first_round["next_action"] is None


@pytest.mark.anyio
async def test_terminal_round_closure_serializes_before_replacement_binding():
    class BlockingTerminalRoundStore(MemoryRoundStateStore):
        def __init__(self):
            super().__init__()
            self.close_started = asyncio.Event()
            self.allow_close = asyncio.Event()

        async def set_run_state(self, run_id, *, state, **kwargs):
            if state == "closed":
                self.close_started.set()
                await self.allow_close.wait()
            return await super().set_run_state(run_id, state=state, **kwargs)

    store = MemoryRunStore()
    round_store = BlockingTerminalRoundStore()
    manager = RunManager(
        store=store,
        round_store=round_store,
        terminal_cleanup_delay=-1,
    )
    first = await manager.create_or_reject("thread-round-terminal-race")
    await manager.set_status(first.run_id, RunStatus.running)

    close_task = asyncio.create_task(manager.set_status(first.run_id, RunStatus.success, terminal_reason="success"))
    await round_store.close_started.wait()
    replacement_task = asyncio.create_task(manager.create_or_reject("thread-round-terminal-race"))
    await asyncio.sleep(0)
    assert not replacement_task.done()

    round_store.allow_close.set()
    assert await close_task is True
    replacement = await replacement_task
    rounds = await round_store.list_by_thread("thread-round-terminal-race")
    first_round = next(row for row in rounds if row["round_id"] == first.round_id)
    replacement_round = next(row for row in rounds if row["round_id"] == replacement.round_id)
    assert first_round["state"] == "closed"
    assert replacement.round_id != first.round_id
    assert replacement_round["parent_round_id"] == first.round_id
    assert replacement_round["current_run_id"] == replacement.run_id


@pytest.mark.anyio
async def test_closed_round_state_is_mechanical_not_quality_verdict_or_authorization():
    round_store = MemoryRoundStateStore()
    manager = RunManager(store=MemoryRunStore(), round_store=round_store, terminal_cleanup_delay=-1)

    first = await manager.create_or_reject(
        "thread-awaiting-decision-contract",
        kwargs={"input": {"messages": [{"role": "user", "content": "inspect system"}]}},
    )
    await manager.set_status(first.run_id, RunStatus.running)
    await manager.set_status(first.run_id, RunStatus.success, terminal_reason="success")
    await manager.update_run_completion(
        first.run_id,
        status="success",
        last_ai_message="Review complete. Run more evidence and opposition checks before committing.",
    )

    rounds = await round_store.list_by_thread("thread-awaiting-decision-contract")
    first_round = next(round for round in rounds if round["round_id"] == first.round_id)
    assert first_round["state"] == "closed"
    assert first_round["next_action"] is None
    assert "PASS" not in str(first_round)
    assert "quality_verdict" not in first_round
    assert "verdict" not in first_round

    second = await manager.create_or_reject(
        "thread-awaiting-decision-contract",
        kwargs={"input": {"messages": [{"role": "user", "content": "continue"}]}},
    )

    assert second.round_id != first.round_id
    context = second.metadata["round_context"]
    assert context["parent_round_id"] == first.round_id
    assert "accepted_next_action" not in context
    assert "next_action" not in context
    assert second.status != RunStatus.running
    assert not round_store.task_lanes

    second_round = next(round for round in await round_store.list_by_thread("thread-awaiting-decision-contract") if round["round_id"] == second.round_id)
    assert second_round["state"] not in {"executing", "validating"}


def test_round_context_keeps_row_next_action_out_of_model_context():
    context = RunManager._round_context_from_info(
        {
            "round_id": "round-1",
            "state": "open",
            "next_action": "Run another review loop.",
        },
        run_id="run-1",
        current_intent="implement the fix",
    )

    assert "accepted_next_action" not in context
    assert "next_action" not in context


def test_native_round_context_labels_parent_intent_as_history_not_authorization():
    text = format_native_round_context_for_model(
        {
            "current_intent": "继续",
            "parent_intent": "完成 DeerFlow 前后端修复",
        }
    )

    assert "Current user goal: 继续" in text
    assert "Previous round user goal (historical fact, not current authorization): 完成 DeerFlow 前后端修复" in text


@pytest.mark.anyio
async def test_task_event_updates_task_lane_outside_visible_history():
    event_store = MemoryRunEventStore()
    round_store = MemoryRoundStateStore()
    round_info = await round_store.bind_run(
        thread_id="thread-1",
        run_id="run-1",
        current_intent="start",
    )
    journal = RunJournal(
        "run-1",
        "thread-1",
        event_store,
        flush_threshold=100,
        round_store=round_store,
        round_id=round_info["round_id"],
    )

    journal.record_task_event(
        {
            "type": "task_started",
            "task_id": "task-1",
            "thread_id": "thread-1",
            "run_id": "run-1",
            "subagent_type": "planner",
            "status": "in_progress",
        }
    )
    await journal.flush()

    assert round_store.task_lanes[("thread-1", "run-1", "task-1")]["role"] == "planner"
    assert round_store.task_lanes[("thread-1", "run-1", "task-1")]["status"] == "in_progress"
    [row] = await event_store.list_messages_by_run("thread-1", "run-1")
    assert row["content"]["round_id"] == round_info["round_id"]


@pytest.mark.anyio
async def test_memory_round_state_preserves_handoff_envelope():
    round_store = MemoryRoundStateStore()
    round_info = await round_store.bind_run(
        thread_id="thread-handoff",
        run_id="run-handoff",
        current_intent="handoff",
    )
    handoff = {
        "sourceRole": "Planner",
        "targetRole": "Evidence",
        "taskOrQuestion": "inspect refs",
        "evidenceRefs": ["docs/command-room/run-protocol.md:25"],
        "evidenceStrength": "Strong",
        "rawInputSha256": "abc123",
    }
    container_facts = {
        "command_room_container": "execution",
        "work_package_id": "package-a",
        "delivery_cycle_index": 1,
        "container_artifact_path": "03-delivery/cycle-01/execution.md",
        "container_artifact_written": True,
        "container_artifact_kind": "execution",
    }

    await round_store.record_task_events(
        [
            {
                "type": "task_completed",
                "thread_id": "thread-handoff",
                "run_id": "run-handoff",
                "task_id": "task-handoff",
                "subagent_type": "evidence",
                "status": "completed",
                "handoff_envelope": handoff,
                **container_facts,
            }
        ]
    )

    [lane] = await round_store.list_task_lanes_by_round(thread_id="thread-handoff", round_id=round_info["round_id"])
    assert lane["handoff"] == {**handoff, **container_facts}


@pytest.mark.anyio
async def test_sql_round_state_preserves_handoff_envelope(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'round-state.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    round_store = RoundStateRepository(async_sessionmaker(engine, expire_on_commit=False))
    round_info = await round_store.bind_run(
        thread_id="thread-sql-handoff",
        run_id="run-sql-handoff",
        current_intent="handoff",
    )
    handoff = {
        "sourceRole": "Planner",
        "targetRole": "Evidence",
        "taskOrQuestion": "inspect refs",
        "artifactRefs": ["outputs/findings.md"],
        "evidenceStrength": "Unverified",
        "rawInputSha256": "def456",
    }
    container_facts = {
        "command_room_container": "review",
        "work_package_id": "package-b",
        "delivery_cycle_index": 1,
        "container_artifact_path": "03-delivery/cycle-01/review.md",
        "container_artifact_written": False,
        "container_artifact_kind": "findings",
    }

    try:
        await round_store.record_task_events(
            [
                {
                    "type": "task_completed",
                    "thread_id": "thread-sql-handoff",
                    "run_id": "run-sql-handoff",
                    "task_id": "task-sql-handoff",
                    "subagent_type": "evidence",
                    "status": "completed",
                    "handoff_envelope": handoff,
                    **container_facts,
                }
            ]
        )

        [lane] = await round_store.list_task_lanes_by_round(thread_id="thread-sql-handoff", round_id=round_info["round_id"])
        assert lane["handoff"] == {**handoff, **container_facts}
        assert "handoff_json" not in lane
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_native_task_lane_preserves_safe_display_metadata():
    round_store = MemoryRoundStateStore()
    round_info = await round_store.bind_run(
        thread_id="thread-display",
        run_id="run-display",
        current_intent="display metadata",
    )

    await round_store.record_task_events(
        [
            {
                "type": "task_started",
                "thread_id": "thread-display",
                "run_id": "run-display",
                "task_id": "task-display",
                "subagent_type": "planner",
                "description": "Inspect the runtime contract",
                "prompt": "must not be persisted",
                "status": "in_progress",
                "started_at": "2026-07-10T01:02:03Z",
            },
            {
                "type": "task_completed",
                "thread_id": "thread-display",
                "run_id": "run-display",
                "task_id": "task-display",
                "status": "completed",
                "result_preview": "Contract is aligned",
                "completed_at": "2026-07-10T01:02:05Z",
                "duration_ms": 2000,
            },
        ]
    )

    [lane] = await round_store.list_task_lanes_by_round(
        thread_id="thread-display",
        round_id=round_info["round_id"],
    )
    assert lane["description"] == "Inspect the runtime contract"
    assert lane["subagent_type"] == "planner"
    assert lane["result"] == "Contract is aligned"
    assert lane["started_at"] == "2026-07-10T01:02:03Z"
    assert lane["finished_at"] == "2026-07-10T01:02:05Z"
    assert lane["duration_ms"] == 2000
    assert "prompt" not in lane


@pytest.mark.anyio
async def test_sql_task_lane_preserves_safe_display_metadata(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'task-lane-display.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    round_store = RoundStateRepository(async_sessionmaker(engine, expire_on_commit=False))
    round_info = await round_store.bind_run(
        thread_id="thread-sql-display",
        run_id="run-sql-display",
        current_intent="display metadata",
    )
    try:
        await round_store.record_task_events(
            [
                {
                    "type": "task_started",
                    "thread_id": "thread-sql-display",
                    "run_id": "run-sql-display",
                    "task_id": "task-sql-display",
                    "subagent_type": "evidence",
                    "description": "Collect evidence",
                    "status": "in_progress",
                    "started_at": "2026-07-10T01:02:03Z",
                },
                {
                    "type": "task_completed",
                    "thread_id": "thread-sql-display",
                    "run_id": "run-sql-display",
                    "task_id": "task-sql-display",
                    "status": "completed",
                    "result_preview": "Evidence collected",
                    "completed_at": "2026-07-10T01:02:06Z",
                    "duration_ms": 3000,
                },
            ]
        )
        [lane] = await round_store.list_task_lanes_by_round(
            thread_id="thread-sql-display",
            round_id=round_info["round_id"],
        )
        assert lane["description"] == "Collect evidence"
        assert lane["subagent_type"] == "evidence"
        assert lane["result"] == "Evidence collected"
        assert lane["duration_ms"] == 3000
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_task_refs_flow_into_native_round_context():
    event_store = MemoryRunEventStore()
    round_store = MemoryRoundStateStore()
    manager = RunManager(store=MemoryRunStore(), round_store=round_store, terminal_cleanup_delay=-1)

    record = await manager.create_or_reject(
        "thread-1",
        kwargs={"input": {"messages": [{"role": "user", "content": "run task"}]}},
    )
    await manager.set_status(record.run_id, RunStatus.running)
    journal = RunJournal(
        record.run_id,
        record.thread_id,
        event_store,
        flush_threshold=100,
        round_store=round_store,
        round_id=record.round_id,
    )
    journal.record_task_event(
        {
            "type": "task_completed",
            "task_id": "task-1",
            "thread_id": record.thread_id,
            "run_id": record.run_id,
            "subagent_type": "evidence",
            "status": "completed",
            "action_result": {
                "output_ref": "outputs/findings.md",
                "evidence_refs": ["command: pytest backend/tests/test_native_round_state.py -q; exit code: 0"],
            },
        }
    )
    await journal.flush()
    await manager.set_status(record.run_id, RunStatus.success, terminal_reason="success")
    await manager.update_run_completion(record.run_id, status="success", last_ai_message="Next: inspect refs.")

    context = record.metadata["round_context"]
    assert context["artifact_refs"] == ["outputs/findings.md"]
    assert context["evidence_refs"] == ["command: pytest backend/tests/test_native_round_state.py -q; exit code: 0"]
    text = format_native_round_context_for_model(context)
    assert text is not None
    assert "Current user goal: run task" in text
    assert "ArtifactRefs: outputs/findings.md" in text
    assert "EvidenceRefs: command: pytest backend/tests/test_native_round_state.py -q; exit code: 0" in text


@pytest.mark.anyio
async def test_native_round_routes_expose_rounds_and_task_lanes():
    round_store = MemoryRoundStateStore()
    manager = RunManager(store=MemoryRunStore(), round_store=round_store, terminal_cleanup_delay=-1)
    user_id = get_effective_user_id()
    record = await manager.create_or_reject(
        "thread-1",
        kwargs={"input": {"messages": [{"role": "user", "content": "route test"}]}},
        user_id=user_id,
    )
    await round_store.record_task_events(
        [
            {
                "type": "task_completed",
                "thread_id": record.thread_id,
                "run_id": record.run_id,
                "task_id": "task-1",
                "subagent_type": "planner",
                "status": "completed",
                "action_result": {"output_ref": "outputs/plan.md"},
                "handoff_envelope": {"targetRole": "Planner", "taskOrQuestion": "plan", "evidenceStrength": "Unverified"},
            }
        ]
    )

    class AppState:
        round_state_store = round_store

    class Request:
        app = type("App", (), {"state": AppState()})()
        state = type("State", (), {"storage_user_id": None})()

    request = Request()
    rounds = await thread_runs.list_rounds.__wrapped__("thread-1", request, limit=50)
    tasks = await thread_runs.list_round_tasks.__wrapped__("thread-1", record.round_id, request, limit=100)

    assert rounds[0].round_id == record.round_id
    assert rounds[0].artifact_refs == ["outputs/plan.md"]
    assert tasks[0].task_id == "task-1"
    assert tasks[0].role == "planner"
    assert tasks[0].handoff == {"targetRole": "Planner", "taskOrQuestion": "plan", "evidenceStrength": "Unverified"}


@pytest.mark.anyio
async def test_runtime_snapshot_repairs_missing_task_lane_from_event_store():
    event_store = MemoryRunEventStore()
    round_store = MemoryRoundStateStore()
    manager = RunManager(store=MemoryRunStore(), round_store=round_store, terminal_cleanup_delay=-1)
    record = await manager.create_or_reject("thread-projection", kwargs={"input": {"messages": [{"role": "user", "content": "run"}]}})
    await event_store.put(
        thread_id=record.thread_id,
        run_id=record.run_id,
        event_type="task_started",
        category="event",
        content={
            "task_id": "task-1",
            "subagent_type": "planner",
            "description": "Build a plan",
            "started_at": "2026-07-10T01:02:03Z",
            "status": "in_progress",
        },
    )
    await event_store.put(
        thread_id=record.thread_id,
        run_id=record.run_id,
        event_type="task_completed",
        category="event",
        content={
            "task_id": "task-1",
            "result_preview": "Plan ready",
            "completed_at": "2026-07-10T01:02:05Z",
            "duration_ms": 2000,
            "status": "completed",
        },
    )

    repaired = await thread_runs._repair_task_event_projection_from_store(event_store=event_store, round_store=round_store, records=[record], round_rows=await round_store.list_by_thread(record.thread_id), task_lane_rows=[], user_id=None)

    assert repaired is True
    [lane] = await round_store.list_task_lanes_by_round(thread_id=record.thread_id, round_id=record.round_id)
    assert lane["task_id"] == "task-1"
    assert lane["status"] == "completed"
    assert lane["description"] == "Build a plan"
    assert lane["subagent_type"] == "planner"
    assert lane["result"] == "Plan ready"
    assert lane["duration_ms"] == 2000
    assert len(await event_store.list_events(record.thread_id, record.run_id, limit=10)) == 2


@pytest.mark.anyio
async def test_runtime_snapshot_projection_repair_pages_past_first_500_task_events():
    event_store = MemoryRunEventStore()
    round_store = MemoryRoundStateStore()
    manager = RunManager(store=MemoryRunStore(), round_store=round_store, terminal_cleanup_delay=-1)
    record = await manager.create_or_reject("thread-projection-pages")
    await round_store.record_task_events(
        [
            {
                "type": "task_started",
                "thread_id": record.thread_id,
                "run_id": record.run_id,
                "task_id": "task-long",
                "status": "in_progress",
            }
        ]
    )
    await event_store.put_batch(
        [
            {
                "thread_id": record.thread_id,
                "run_id": record.run_id,
                "event_type": "task_started",
                "category": "event",
                "content": {"task_id": "task-long", "status": "in_progress", "message_index": index},
            }
            for index in range(500)
        ]
        + [
            {
                "thread_id": record.thread_id,
                "run_id": record.run_id,
                "event_type": "task_completed",
                "category": "event",
                "content": {"task_id": "task-long", "status": "completed", "result_preview": "done"},
            }
        ]
    )

    repaired = await thread_runs._repair_task_event_projection_from_store(
        event_store=event_store,
        round_store=round_store,
        records=[record],
        round_rows=await round_store.list_by_thread(record.thread_id),
        task_lane_rows=await round_store.list_task_lanes_by_round(thread_id=record.thread_id, round_id=record.round_id),
        user_id=None,
    )

    [lane] = await round_store.list_task_lanes_by_round(thread_id=record.thread_id, round_id=record.round_id)
    assert repaired is True
    assert lane["status"] == "completed"
    assert lane["result"] == "done"


@pytest.mark.anyio
async def test_projection_repair_skips_identity_mismatch_and_missing_round_mapping():
    event_store = MemoryRunEventStore()
    round_store = MemoryRoundStateStore()
    manager = RunManager(store=MemoryRunStore(), round_store=round_store, terminal_cleanup_delay=-1)
    record = await manager.create_or_reject("thread-projection-skip", kwargs={"input": {"messages": [{"role": "user", "content": "run"}]}})
    await event_store.put(thread_id=record.thread_id, run_id="other-run", event_type="task_completed", category="event", content={"task_id": "task-x", "status": "completed"})

    repaired = await thread_runs._repair_task_event_projection_from_store(event_store=event_store, round_store=round_store, records=[record], round_rows=[], task_lane_rows=[], user_id=None)

    assert repaired is False
    assert await round_store.list_task_lanes_by_round(thread_id=record.thread_id, round_id=record.round_id) == []


@pytest.mark.anyio
async def test_projection_repair_terminal_event_updates_active_lane_without_run_status_pass_and_is_idempotent():
    event_store = MemoryRunEventStore()
    round_store = MemoryRoundStateStore()
    manager = RunManager(store=MemoryRunStore(), round_store=round_store, terminal_cleanup_delay=-1)
    record = await manager.create_or_reject("thread-projection-terminal", kwargs={"input": {"messages": [{"role": "user", "content": "run"}]}})
    await round_store.record_task_events([{"type": "task_started", "thread_id": record.thread_id, "run_id": record.run_id, "task_id": "task-1", "status": "in_progress"}])
    await event_store.put(thread_id=record.thread_id, run_id=record.run_id, event_type="task_completed", category="event", content={"task_id": "task-1", "status": "completed"})
    event_count = len(await event_store.list_events(record.thread_id, record.run_id, limit=10))

    for _ in range(2):
        task_lanes = await round_store.list_task_lanes_by_round(thread_id=record.thread_id, round_id=record.round_id)
        await thread_runs._repair_task_event_projection_from_store(event_store=event_store, round_store=round_store, records=[record], round_rows=await round_store.list_by_thread(record.thread_id), task_lane_rows=task_lanes, user_id=None)

    [lane] = await round_store.list_task_lanes_by_round(thread_id=record.thread_id, round_id=record.round_id)
    assert lane["status"] == "completed"
    assert record.status == RunStatus.pending
    assert "PASS" not in str(lane)
    assert len(await event_store.list_events(record.thread_id, record.run_id, limit=10)) == event_count


@pytest.mark.anyio
async def test_projection_repair_does_not_downgrade_terminal_lane_from_stale_started_event():
    event_store = MemoryRunEventStore()
    round_store = MemoryRoundStateStore()
    manager = RunManager(store=MemoryRunStore(), round_store=round_store, terminal_cleanup_delay=-1)
    record = await manager.create_or_reject("thread-projection-terminal-strong", kwargs={"input": {"messages": [{"role": "user", "content": "run"}]}})
    await round_store.record_task_events(
        [
            {
                "type": "task_completed",
                "thread_id": record.thread_id,
                "run_id": record.run_id,
                "task_id": "task-1",
                "status": "completed",
            }
        ]
    )
    await event_store.put(
        thread_id=record.thread_id,
        run_id=record.run_id,
        event_type="task_started",
        category="event",
        content={"task_id": "task-1", "status": "in_progress"},
    )

    repaired = await thread_runs._repair_task_event_projection_from_store(
        event_store=event_store,
        round_store=round_store,
        records=[record],
        round_rows=await round_store.list_by_thread(record.thread_id),
        task_lane_rows=await round_store.list_task_lanes_by_round(thread_id=record.thread_id, round_id=record.round_id),
        user_id=None,
    )

    [lane] = await round_store.list_task_lanes_by_round(thread_id=record.thread_id, round_id=record.round_id)
    assert repaired is False
    assert lane["status"] == "completed"


@pytest.mark.anyio
async def test_projection_repair_scopes_same_task_id_to_run_id():
    event_store = MemoryRunEventStore()
    round_store = MemoryRoundStateStore()
    thread_id = "thread-projection-run-scope"
    first = await round_store.bind_run(thread_id=thread_id, run_id="run-1", current_intent="first")
    await round_store.set_run_state(
        "run-1",
        thread_id=thread_id,
        user_id=None,
        round_id=first["round_id"],
        state="closed",
        event_type="run.completed",
    )
    second = await round_store.bind_run(thread_id=thread_id, run_id="run-2", current_intent="second")
    records = [
        RunRecord(run_id="run-1", thread_id=thread_id, assistant_id="lead_agent", status=RunStatus.success, on_disconnect="continue", round_id=first["round_id"]),
        RunRecord(run_id="run-2", thread_id=thread_id, assistant_id="lead_agent", status=RunStatus.error, on_disconnect="continue", round_id=second["round_id"]),
    ]
    for run_id, event_type, status in [
        ("run-1", "task_completed", "completed"),
        ("run-2", "task_failed", "failed"),
    ]:
        await event_store.put(
            thread_id=thread_id,
            run_id=run_id,
            event_type=event_type,
            category="event",
            content={"task_id": "task-shared", "status": status},
        )

    repaired = await thread_runs._repair_task_event_projection_from_store(
        event_store=event_store,
        round_store=round_store,
        records=records,
        round_rows=await round_store.list_by_thread(thread_id),
        task_lane_rows=[],
        user_id=None,
    )

    first_lanes = await round_store.list_task_lanes_by_round(thread_id=thread_id, round_id=first["round_id"])
    second_lanes = await round_store.list_task_lanes_by_round(thread_id=thread_id, round_id=second["round_id"])
    assert repaired is True
    assert [(lane["run_id"], lane["task_id"], lane["status"]) for lane in first_lanes] == [("run-1", "task-shared", "completed")]
    assert [(lane["run_id"], lane["task_id"], lane["status"]) for lane in second_lanes] == [("run-2", "task-shared", "failed")]


async def _assert_background_recovery_facts_survive_task_event_replay(round_store):
    await round_store.bind_run(thread_id="thread-background", run_id="run-background")
    admission = {
        "version": 1,
        "thread_id": "thread-background",
        "source_run_id": "run-background",
        "task_id": "task-background",
        "work_package_id": "package-a",
        "outcome": None,
        "wake": {"state": "pending", "attempts": 0},
    }
    await round_store.record_task_events(
        [
            {
                "type": "task_started",
                "thread_id": "thread-background",
                "run_id": "run-background",
                "task_id": "task-background",
                "status": "in_progress",
                "handoff_envelope": {"background_recovery": admission},
            },
            {
                "type": "task_completed",
                "thread_id": "thread-background",
                "run_id": "run-background",
                "task_id": "task-background",
                "status": "completed",
                "handoff_envelope": {
                    "background_recovery": {
                        **admission,
                        "outcome": {"status": "completed", "result": "done"},
                        "wake": {"state": "pending", "attempts": 0},
                    }
                },
            },
            {
                "type": "task_started",
                "thread_id": "thread-background",
                "run_id": "run-background",
                "task_id": "task-background",
                "status": "in_progress",
            },
        ]
    )

    rows = await round_store.list_background_task_lanes()
    assert len(rows) == 1
    lane = rows[0]
    assert lane["status"] == "completed"
    assert lane["handoff"]["background_recovery"]["work_package_id"] == "package-a"
    assert lane["handoff"]["background_recovery"]["outcome"]["result"] == "done"


@pytest.mark.anyio
async def test_memory_background_recovery_facts_survive_task_event_replay():
    await _assert_background_recovery_facts_survive_task_event_replay(MemoryRoundStateStore())


@pytest.mark.anyio
async def test_sql_background_recovery_facts_survive_task_event_replay(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'background-recovery.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        await _assert_background_recovery_facts_survive_task_event_replay(RoundStateRepository(async_sessionmaker(engine, expire_on_commit=False)))
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_sql_background_wake_claim_is_atomic_and_expired_claims_are_recoverable(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'background-wake-claim.db'}"
    first_engine = create_async_engine(database_url, connect_args={"timeout": 30})
    second_engine = create_async_engine(database_url, connect_args={"timeout": 30})
    async with first_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    first = RoundStateRepository(async_sessionmaker(first_engine, expire_on_commit=False))
    second = RoundStateRepository(async_sessionmaker(second_engine, expire_on_commit=False))
    try:
        await first.bind_run(thread_id="thread-wake-claim", run_id="run-wake-claim")
        await first.record_task_events(
            [
                {
                    "type": "task_started",
                    "thread_id": "thread-wake-claim",
                    "run_id": "run-wake-claim",
                    "task_id": "task-wake-claim",
                    "status": "in_progress",
                }
            ]
        )
        now = datetime.now(UTC)
        lease_expires_at = now + timedelta(minutes=1)
        claims = await asyncio.gather(
            first.claim_background_wake(
                thread_id="thread-wake-claim",
                run_id="run-wake-claim",
                task_id="task-wake-claim",
                user_id=None,
                claim_id="gateway-a",
                now=now,
                lease_expires_at=lease_expires_at,
            ),
            second.claim_background_wake(
                thread_id="thread-wake-claim",
                run_id="run-wake-claim",
                task_id="task-wake-claim",
                user_id=None,
                claim_id="gateway-b",
                now=now,
                lease_expires_at=lease_expires_at,
            ),
        )

        assert claims.count(True) == 1
        assert not await second.claim_background_wake(
            thread_id="thread-wake-claim",
            run_id="run-wake-claim",
            task_id="task-wake-claim",
            user_id=None,
            claim_id="gateway-b-retry",
            now=now,
            lease_expires_at=lease_expires_at,
        )
        assert await second.claim_background_wake(
            thread_id="thread-wake-claim",
            run_id="run-wake-claim",
            task_id="task-wake-claim",
            user_id=None,
            claim_id="gateway-after-crash",
            now=lease_expires_at + timedelta(seconds=1),
            lease_expires_at=lease_expires_at + timedelta(minutes=1),
        )
    finally:
        await first_engine.dispose()
        await second_engine.dispose()
