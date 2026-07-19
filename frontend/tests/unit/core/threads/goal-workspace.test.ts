import { expect, test } from "@rstest/core";

import {
  goalCellRows,
  parseGoalTree,
  parseGoalWorkspace,
  parseGoalWorkspaceHistory,
} from "@/core/threads/goal-workspace";

test("parseGoalWorkspace preserves complete opaque records and result bodies", () => {
  const completeResult = `complete result\n${"事实".repeat(8_000)}`;
  const workspace = parseGoalWorkspace(
    {
      thread_id: "thread-1",
      goal_mandate: {
        revision: 2,
        body: "Human direction in full.",
        content_hash: "mandate-hash",
        author_run_id: "run-1",
        created_at: "2026-07-19T10:00:00Z",
      },
      operating_brief: {
        revision: 4,
        body: "Chair brief in full.",
        content_hash: "brief-hash",
        author_run_id: "run-2",
        created_at: "2026-07-19T10:01:00Z",
      },
      organization_map: {
        revision: 5,
        body: "Current temporary organization in full.",
        content_hash: "organization-hash",
        author_run_id: "run-2",
        created_at: "2026-07-19T10:01:30Z",
      },
      acknowledged_through_seq: 3,
      notified_through_seq: 8,
      results: [
        {
          revision: 8,
          body: completeResult,
          content_hash: "result-hash",
          author_run_id: "child-run",
          created_at: "2026-07-19T10:02:00Z",
          metadata: { role: "planner", task_id: "task-1" },
        },
      ],
    },
    "thread-1",
  );

  expect(workspace.goalMandate?.body).toBe("Human direction in full.");
  expect(workspace.operatingBrief?.body).toBe("Chair brief in full.");
  expect(workspace.organizationMap?.body).toBe(
    "Current temporary organization in full.",
  );
  expect(workspace.results[0]?.body).toBe(completeResult);
  expect(workspace.results[0]?.metadata).toEqual({
    role: "planner",
    task_id: "task-1",
  });
  expect(workspace.acknowledgedThroughSeq).toBe(3);
  expect(workspace.notifiedThroughSeq).toBe(8);
});

test("parseGoalWorkspaceHistory preserves raw facts and its pagination cursor", () => {
  const acknowledgedResult = `complete acknowledged result\n${"事实".repeat(8_000)}`;
  const history = parseGoalWorkspaceHistory(
    {
      thread_id: "thread-1",
      events: [
        {
          revision: 12,
          event_type: "result.inbox.acknowledged",
          body: "The Chair explicitly acknowledged result inbox through sequence 11.",
          content_hash: "acknowledgement-hash",
          author_run_id: "chair-run",
          created_at: "2026-07-19T10:03:00Z",
          metadata: { through_seq: 11 },
        },
        {
          revision: 11,
          event_type: "result.received",
          body: acknowledgedResult,
          content_hash: "result-hash",
          author_run_id: "child-run",
          created_at: "2026-07-19T10:02:00Z",
          metadata: { task_id: "task-1", role: "fact-finder" },
        },
      ],
      next_before_revision: 11,
    },
    "thread-1",
  );

  expect(history.events).toEqual([
    expect.objectContaining({
      revision: 12,
      eventType: "result.inbox.acknowledged",
      metadata: { through_seq: 11 },
    }),
    expect.objectContaining({
      revision: 11,
      eventType: "result.received",
      body: acknowledgedResult,
      metadata: { task_id: "task-1", role: "fact-finder" },
    }),
  ]);
  expect(history.nextBeforeRevision).toBe(11);
});

test("goalCellRows renders recursive relationships and settles malformed cycles", () => {
  const tree = parseGoalTree({
    root_thread_id: "root",
    cells: [
      {
        thread_id: "cell-a",
        parent_thread_id: "root",
        parent_run_id: "run-root",
        display_name: "A",
        runtime_status: "running",
        capability_refs: ["read-only"],
        workspace_ref: null,
        created_at: "2026-07-19T10:00:00Z",
        updated_at: "2026-07-19T10:00:00Z",
      },
      {
        thread_id: "cell-b",
        parent_thread_id: "cell-a",
        parent_run_id: "run-a",
        display_name: "B",
        runtime_status: "idle",
        capability_refs: [],
        workspace_ref: null,
        created_at: "2026-07-19T10:01:00Z",
        updated_at: "2026-07-19T10:01:00Z",
      },
      {
        thread_id: "cycle-a",
        parent_thread_id: "cycle-b",
        parent_run_id: "cycle-run-b",
        display_name: null,
        runtime_status: "idle",
        capability_refs: [],
        workspace_ref: null,
        created_at: "2026-07-19T10:02:00Z",
        updated_at: "2026-07-19T10:02:00Z",
      },
      {
        thread_id: "cycle-b",
        parent_thread_id: "cycle-a",
        parent_run_id: "cycle-run-a",
        display_name: null,
        runtime_status: "idle",
        capability_refs: [],
        workspace_ref: null,
        created_at: "2026-07-19T10:03:00Z",
        updated_at: "2026-07-19T10:03:00Z",
      },
    ],
  });

  expect(
    goalCellRows(tree).map(({ threadId, depth }) => [threadId, depth]),
  ).toEqual([
    ["cell-a", 0],
    ["cell-b", 1],
    ["cycle-a", 0],
    ["cycle-b", 1],
  ]);
});
