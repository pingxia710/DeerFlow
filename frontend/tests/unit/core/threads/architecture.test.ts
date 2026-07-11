import { expect, test } from "@rstest/core";

import {
  shouldApplyOwnerThreadEffect,
  shouldApplyVisibleThreadEffect,
  shouldApplyVisibleViewEffect,
} from "@/core/threads/effect-policy";
import { shouldAutoContinueRunHistory } from "@/core/threads/message-history";
import {
  createThreadViewScope,
  hasStrongCommandRoomIdentity,
  isSameThreadViewScope,
} from "@/core/threads/owner-scope";
import { isThreadScopedQueryKey, queryKeys } from "@/core/threads/query-keys";
import {
  getBackgroundRunProbeDelay,
  resolveRunStreamRecoveryErrorOwner,
} from "@/core/threads/run-recovery";
import { asTaskEvent } from "@/core/threads/task-events";

test("view scope identity separates route owners even when thread ids match", () => {
  const normal = createThreadViewScope({
    runtimeScope: "chat",
    runtimeKey: "chat:thread-1",
    displayThreadId: "thread-1",
  });
  const agent = createThreadViewScope({
    runtimeScope: "agent:writer",
    runtimeKey: "agent-chat:writer:thread-1",
    displayThreadId: "thread-1",
  });

  expect(isSameThreadViewScope(normal, normal)).toBe(true);
  expect(isSameThreadViewScope(normal, agent)).toBe(false);
});

test("command-room strong identity requires thread run and round", () => {
  expect(
    hasStrongCommandRoomIdentity({
      threadId: "thread-1",
      runId: "run-1",
      roundId: "round-1",
    }),
  ).toBe(true);
  expect(
    hasStrongCommandRoomIdentity({
      threadId: "thread-1",
      runId: "run-1",
    }),
  ).toBe(false);
});

test("visible effects require the same view but background thread effects do not", () => {
  const viewA = createThreadViewScope({
    runtimeScope: "chat",
    runtimeKey: "chat:thread-a",
    displayThreadId: "thread-a",
  });
  const viewB = createThreadViewScope({
    runtimeScope: "chat",
    runtimeKey: "chat:thread-b",
    displayThreadId: "thread-b",
  });

  expect(
    shouldApplyVisibleViewEffect({ effectView: viewA, currentView: viewB }),
  ).toBe(false);
  expect(
    shouldApplyOwnerThreadEffect({
      effectThreadId: "thread-a",
      ownerThreadId: "thread-a",
    }),
  ).toBe(true);
});

test("visible thread effects accept the live or committed route owner only", () => {
  expect(
    shouldApplyVisibleThreadEffect({
      effectThreadId: "thread-a",
      visibleThreadId: "thread-b",
      committedThreadId: "thread-a",
    }),
  ).toBe(true);
  expect(
    shouldApplyVisibleThreadEffect({
      effectThreadId: "thread-a",
      visibleThreadId: "thread-b",
      committedThreadId: "thread-c",
    }),
  ).toBe(false);
});

test("query key factory preserves existing keys and identifies deletion scope", () => {
  expect(queryKeys.thread.runs("thread-1")).toEqual([
    "thread",
    "thread-1",
    "runs",
  ]);
  expect(queryKeys.thread.metadata("thread-1", true)).toEqual([
    "thread",
    "metadata",
    "thread-1",
    true,
  ]);
  expect(queryKeys.thread.artifact("thread-1", "/report.md", false)).toEqual([
    "artifact",
    "/report.md",
    "thread-1",
    false,
  ]);
  expect(queryKeys.thread.capabilitySnapshot("thread-1")).toEqual([
    "capability-snapshot",
    "thread-1",
  ]);

  expect(
    isThreadScopedQueryKey(
      queryKeys.thread.runtimeSnapshot("thread-1"),
      "thread-1",
    ),
  ).toBe(true);
  expect(
    isThreadScopedQueryKey(
      queryKeys.thread.runtimeSnapshot("thread-2"),
      "thread-1",
    ),
  ).toBe(false);
  expect(
    isThreadScopedQueryKey(
      queryKeys.thread.capabilitySnapshot("thread-1"),
      "thread-1",
    ),
  ).toBe(true);
});

test("domain modules expose pure history, recovery, and task-event boundaries", () => {
  expect(
    shouldAutoContinueRunHistory({
      hasMoreUnloadedRuns: true,
      visibleMessageCount: 1,
      consecutiveEmptyLoads: 0,
    }),
  ).toBe(true);
  expect(getBackgroundRunProbeDelay(2)).toBe(10_000);
  expect(
    resolveRunStreamRecoveryErrorOwner(
      new Error("disconnected"),
      "thread-1",
      "run-1",
    ),
  ).toEqual({ threadId: "thread-1", runId: "run-1" });
  expect(
    asTaskEvent({
      schema_version: "deerflow.task-event/v1",
      event_type: "task_started",
      task_id: "task-1",
      thread_id: "thread-1",
      run_id: "run-1",
    }),
  ).not.toBeNull();
});
