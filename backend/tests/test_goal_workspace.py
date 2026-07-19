"""Goal Mandate and Operating Brief stay factual, durable, and owner-scoped."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from deerflow.agents.middlewares.round_context_middleware import (
    format_goal_workspace_context_for_model,
)
from deerflow.persistence.workspace_event import (
    GOAL_MANDATE_REVISED,
    OPERATING_BRIEF_REVISED,
    ORGANIZATION_MAP_REVISED,
    RESULT_RECEIVED,
    RESULTS_NOTIFIED,
    MemoryWorkspaceEventStore,
    WorkspaceEventConflictError,
    WorkspaceEventRepository,
)
from deerflow.tools.builtins.goal_workspace_tool import (
    acknowledge_workspace_results_tool,
    read_goal_workspace_history_tool,
    read_workspace_results_tool,
    record_goal_workspace_tool,
)


@pytest.fixture
async def sql_store(tmp_path):
    from deerflow.persistence.engine import (
        close_engine,
        get_session_factory,
        init_engine,
    )

    await init_engine(
        "sqlite",
        url=f"sqlite+aiosqlite:///{tmp_path / 'goal-workspace.db'}",
        sqlite_dir=str(tmp_path),
    )
    yield WorkspaceEventRepository(get_session_factory())
    await close_engine()


@pytest.mark.anyio
async def test_sql_store_keeps_append_only_revisions_and_owner_isolation(sql_store):
    mandate = await sql_store.append(
        thread_id="thread-1",
        user_id="user-1",
        event_type=GOAL_MANDATE_REVISED,
        body="Explore the direction autonomously within local-only permissions.",
        author_run_id="run-1",
        event_id="mandate-1",
    )
    brief = await sql_store.append(
        thread_id="thread-1",
        user_id="user-1",
        event_type=OPERATING_BRIEF_REVISED,
        body="Current plan and unresolved facts.",
        author_run_id="run-2",
        event_id="brief-1",
    )
    organization_map = await sql_store.append(
        thread_id="thread-1",
        user_id="user-1",
        event_type=ORGANIZATION_MAP_REVISED,
        body="Current temporary workstreams and return paths.",
        author_run_id="run-2",
        event_id="organization-1",
    )

    assert brief["revision"] > mandate["revision"]
    assert (
        await sql_store.append(
            thread_id="thread-1",
            user_id="user-1",
            event_type=OPERATING_BRIEF_REVISED,
            body="Current plan and unresolved facts.",
            author_run_id="run-2",
            event_id="brief-1",
        )
        == brief
    )
    assert (await sql_store.current_context(thread_id="thread-1", user_id="user-1")) == {
        "goal_mandate": mandate,
        "operating_brief": brief,
        "organization_map": organization_map,
    }
    assert await sql_store.current_context(thread_id="thread-1", user_id="user-2") == {
        "goal_mandate": None,
        "operating_brief": None,
        "organization_map": None,
    }

    with pytest.raises(WorkspaceEventConflictError):
        await sql_store.append(
            thread_id="thread-1",
            user_id="user-1",
            event_type=OPERATING_BRIEF_REVISED,
            body="Different body under the same factual identity.",
            author_run_id="run-2",
            event_id="brief-1",
        )


@pytest.mark.anyio
async def test_sql_result_inbox_preserves_envelopes_until_explicit_ack(sql_store):
    first = await sql_store.append(
        thread_id="thread-1",
        user_id="user-1",
        event_type=RESULT_RECEIVED,
        body="Complete planner result.",
        author_run_id="run-1",
        event_id="result-1",
        metadata={"task_id": "task-1", "role": "planner"},
    )
    second = await sql_store.append(
        thread_id="thread-1",
        user_id="user-1",
        event_type=RESULT_RECEIVED,
        body="Complete opposition result.",
        author_run_id="run-1",
        event_id="result-2",
        metadata={"task_id": "task-2", "role": "opposition"},
    )
    await sql_store.append(
        thread_id="thread-1",
        user_id="user-1",
        event_type=RESULTS_NOTIFIED,
        body=f"Delivered through {second['revision']}.",
        event_id="notification-1",
        metadata={"through_seq": second["revision"]},
    )

    inbox = await sql_store.result_inbox(
        thread_id="thread-1",
        user_id="user-1",
    )
    assert [row["body"] for row in inbox["results"]] == [
        "Complete planner result.",
        "Complete opposition result.",
    ]
    assert inbox["acknowledged_through_seq"] == 0
    assert inbox["notified_through_seq"] == second["revision"]
    assert (
        await sql_store.pending_results(
            thread_id="thread-1",
            user_id="user-1",
        )
        == []
    )

    await sql_store.acknowledge_results(
        thread_id="thread-1",
        user_id="user-1",
        through_seq=second["revision"],
        author_run_id="chair-run",
        event_id="ack-1",
    )
    assert (
        await sql_store.result_inbox(
            thread_id="thread-1",
            user_id="user-1",
        )
    )["results"] == []
    assert [
        row["revision"]
        for row in (
            await sql_store.result_inbox(
                thread_id="thread-1",
                user_id="user-1",
                after_seq=0,
            )
        )["results"]
    ] == [first["revision"], second["revision"]]
    with pytest.raises(ValueError, match="outside the factual inbox"):
        await sql_store.acknowledge_results(
            thread_id="thread-1",
            user_id="user-1",
            through_seq=first["revision"],
            author_run_id="chair-run",
            event_id="ack-regression",
        )


async def _assert_history_is_owner_scoped_and_paged(store):
    mandate = await store.append(
        thread_id="thread-1",
        user_id="user-1",
        event_type=GOAL_MANDATE_REVISED,
        body="Original human direction in full.",
        author_run_id="run-mandate",
        event_id="history-mandate",
        metadata={"source": "human"},
    )
    other_owner = await store.append(
        thread_id="thread-1",
        user_id="user-2",
        event_type=OPERATING_BRIEF_REVISED,
        body="This owner must not see user-1 facts.",
        author_run_id="other-run",
        event_id="history-other-owner",
        metadata={"source": "other-owner"},
    )
    brief = await store.append(
        thread_id="thread-1",
        user_id="user-1",
        event_type=OPERATING_BRIEF_REVISED,
        body="Older Chair brief in full.",
        author_run_id="run-brief",
        event_id="history-brief",
        metadata={"reason": "first plan"},
    )
    result = await store.append(
        thread_id="thread-1",
        user_id="user-1",
        event_type=RESULT_RECEIVED,
        body="Complete child result retained without truncation.",
        author_run_id="child-run",
        event_id="history-result",
        metadata={"task_id": "task-history", "role": "fact-finder"},
    )
    acknowledgement = await store.acknowledge_results(
        thread_id="thread-1",
        user_id="user-1",
        through_seq=result["revision"],
        author_run_id="chair-run",
        event_id="history-acknowledgement",
    )

    newest = await store.history(
        thread_id="thread-1",
        user_id="user-1",
        limit=2,
    )
    older = await store.history(
        thread_id="thread-1",
        user_id="user-1",
        before_revision=newest["next_before_revision"],
        limit=2,
    )
    other_owner_page = await store.history(
        thread_id="thread-1",
        user_id="user-2",
        limit=20,
    )

    assert [row["revision"] for row in newest["events"]] == [
        acknowledgement["revision"],
        result["revision"],
    ]
    assert newest["next_before_revision"] == result["revision"]
    assert [row["revision"] for row in older["events"]] == [
        brief["revision"],
        mandate["revision"],
    ]
    for actual, original in zip(
        [*newest["events"], *older["events"]],
        [acknowledgement, result, brief, mandate],
        strict=True,
    ):
        assert actual["body"] == original["body"]
        assert actual["content_hash"] == original["content_hash"]
        assert actual["metadata"] == original["metadata"]
        assert actual["author_run_id"] == original["author_run_id"]
        assert actual["created_at"] == original["created_at"]
    assert older["next_before_revision"] is None
    assert {row["revision"] for row in newest["events"]}.isdisjoint({row["revision"] for row in older["events"]})
    assert [row["revision"] for row in other_owner_page["events"]] == [other_owner["revision"]]
    assert all(row["body"] != other_owner["body"] for row in [*newest["events"], *older["events"]])


@pytest.mark.anyio
async def test_sql_history_is_owner_scoped_and_paged(sql_store):
    await _assert_history_is_owner_scoped_and_paged(sql_store)


@pytest.mark.anyio
async def test_memory_history_is_owner_scoped_and_paged():
    await _assert_history_is_owner_scoped_and_paged(MemoryWorkspaceEventStore())


@pytest.mark.anyio
async def test_chair_tool_records_verbatim_and_is_idempotent():
    store = MemoryWorkspaceEventStore()
    runtime = SimpleNamespace(
        context={
            "agent_name": "command-room",
            "thread_id": "thread-1",
            "run_id": "run-1",
            "user_id": "user-1",
            "__workspace_event_store": store,
        }
    )
    body = "## Current Operating Brief\n\nKeep this complete natural-language text."
    organization_body = "## Current Organization Map\n\nKeep this complete natural-language text."

    first = await record_goal_workspace_tool.coroutine(
        runtime=runtime,
        kind="operating_brief",
        body=body,
        tool_call_id="call-1",
    )
    second = await record_goal_workspace_tool.coroutine(
        runtime=runtime,
        kind="operating_brief",
        body=body,
        tool_call_id="call-1",
    )
    organization = await record_goal_workspace_tool.coroutine(
        runtime=runtime,
        kind="organization_map",
        body=organization_body,
        tool_call_id="call-2",
    )
    context = await store.current_context(thread_id="thread-1", user_id="user-1")

    assert first.content == second.content
    assert "Recorded organization_map revision" in organization.content
    assert context["operating_brief"]["body"] == body
    assert context["organization_map"]["body"] == organization_body
    assert len(await store.list_by_thread("thread-1", user_id="user-1")) == 2


@pytest.mark.anyio
async def test_chair_reads_without_acknowledging_then_explicitly_acknowledges():
    store = MemoryWorkspaceEventStore()
    runtime = SimpleNamespace(
        context={
            "agent_name": "command-room",
            "thread_id": "thread-1",
            "run_id": "chair-run",
            "user_id": "user-1",
            "__workspace_event_store": store,
        }
    )
    first = await store.append(
        thread_id="thread-1",
        user_id="user-1",
        event_type=RESULT_RECEIVED,
        body="First full result.",
        event_id="result-1",
        metadata={"task_id": "task-1"},
    )
    second = await store.append(
        thread_id="thread-1",
        user_id="user-1",
        event_type=RESULT_RECEIVED,
        body="Second full result.",
        event_id="result-2",
        metadata={"task_id": "task-2"},
    )

    message = await read_workspace_results_tool.coroutine(
        runtime=runtime,
        tool_call_id="read-1",
    )
    inbox_after_read = await store.result_inbox(
        thread_id="thread-1",
        user_id="user-1",
    )

    assert "First full result." in message.content
    assert "Second full result." in message.content
    assert inbox_after_read["acknowledged_through_seq"] == 0
    assert [row["revision"] for row in inbox_after_read["results"]] == [
        first["revision"],
        second["revision"],
    ]

    acknowledgement = await acknowledge_workspace_results_tool.coroutine(
        runtime=runtime,
        through_seq=second["revision"],
        tool_call_id="ack-1",
    )
    inbox_after_ack = await store.result_inbox(
        thread_id="thread-1",
        user_id="user-1",
    )
    assert f"through sequence {second['revision']}" in acknowledgement.content
    assert inbox_after_ack["acknowledged_through_seq"] == second["revision"]
    assert inbox_after_ack["results"] == []


@pytest.mark.anyio
async def test_chair_reads_raw_history_without_mutating_or_accepting_results():
    store = MemoryWorkspaceEventStore()
    runtime = SimpleNamespace(
        context={
            "agent_name": "command-room",
            "thread_id": "thread-1",
            "run_id": "chair-run",
            "user_id": "user-1",
            "__workspace_event_store": store,
        }
    )
    mandate = await store.append(
        thread_id="thread-1",
        user_id="user-1",
        event_type=GOAL_MANDATE_REVISED,
        body="Complete older mandate.",
        author_run_id="run-1",
        event_id="mandate-history-tool",
    )
    result = await store.append(
        thread_id="thread-1",
        user_id="user-1",
        event_type=RESULT_RECEIVED,
        body="Complete acknowledged result remains available.",
        author_run_id="child-run",
        event_id="result-history-tool",
        metadata={"task_id": "task-1"},
    )
    acknowledgement = await store.acknowledge_results(
        thread_id="thread-1",
        user_id="user-1",
        through_seq=result["revision"],
        author_run_id="chair-run",
        event_id="ack-history-tool",
    )
    before = await store.list_by_thread("thread-1", user_id="user-1")

    newest = await read_goal_workspace_history_tool.coroutine(
        runtime=runtime,
        tool_call_id="history-read-newest",
        limit=1,
    )
    result_page = await read_goal_workspace_history_tool.coroutine(
        runtime=runtime,
        tool_call_id="history-read-result",
        before_revision=acknowledgement["revision"],
        limit=1,
    )
    older = await read_goal_workspace_history_tool.coroutine(
        runtime=runtime,
        tool_call_id="history-read-older",
        before_revision=result["revision"],
        limit=1,
    )
    after = await store.list_by_thread("thread-1", user_id="user-1")
    non_chair = await read_goal_workspace_history_tool.coroutine(
        runtime=SimpleNamespace(context={"agent_name": "executor"}),
        tool_call_id="history-read-non-chair",
    )

    assert f"revision: {acknowledgement['revision']}" in newest.content
    assert "event_type: result.inbox.acknowledged" in newest.content
    assert f"next_before_revision: {acknowledgement['revision']}" in newest.content
    assert f"revision: {result['revision']}" in result_page.content
    assert "Complete acknowledged result remains available." in result_page.content
    assert f"revision: {mandate['revision']}" in older.content
    assert "Complete older mandate." in older.content
    assert before == after
    assert "unavailable" in non_chair.content


def test_goal_workspace_context_preserves_complete_ai_authored_text():
    text = format_goal_workspace_context_for_model(
        {
            "goal_mandate": {"revision": 2, "body": "Human direction in full."},
            "operating_brief": {
                "revision": 7,
                "body": "Chair plan, decisions, results, and unresolved facts in full.",
            },
            "organization_map": {
                "revision": 9,
                "body": "Chair temporary organization in full.",
            },
        }
    )

    assert text is not None
    assert "Goal Mandate (revision 2)" in text
    assert "Human direction in full." in text
    assert "Current Operating Brief (revision 7)" in text
    assert "Chair plan, decisions, results, and unresolved facts in full." in text
    assert "Current Organization Map (revision 9)" in text
    assert "Chair temporary organization in full." in text
