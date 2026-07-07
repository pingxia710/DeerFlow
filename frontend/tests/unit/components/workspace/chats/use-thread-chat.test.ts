import { expect, test } from "@rstest/core";

import { resolveThreadChatRouteSync } from "@/components/workspace/chats/use-thread-chat";
import {
  threadRuntimeSnapshotQueryKey,
  threadRunsQueryKey,
} from "@/core/threads/hooks";

test("useThreadChat route sync trusts the committed browser thread URL over stale /new route state", () => {
  const nextState = resolveThreadChatRouteSync({
    committedPathname: "/workspace/chats/created-thread",
    threadIdFromPath: "new",
    currentThreadId: "created-thread",
    newThreadId: "fresh-new-uuid",
    createThreadId: () => "unexpected-new-uuid",
  });

  expect(nextState).toEqual({
    threadId: "created-thread",
    isNewThread: false,
    newThreadId: null,
  });
});

test("useThreadChat route sync parses committed agent chat thread URLs when params are stale", () => {
  const nextState = resolveThreadChatRouteSync({
    committedPathname: "/workspace/agents/command-room/chats/agent-thread",
    threadIdFromPath: "new",
    currentThreadId: "pending-thread",
    newThreadId: "fresh-new-uuid",
    createThreadId: () => "unexpected-new-uuid",
  });

  expect(nextState.threadId).toBe("agent-thread");
  expect(nextState.isNewThread).toBe(false);
  expect(nextState.threadId).not.toBe("fresh-new-uuid");
});

test("useThreadChat route sync creates a fresh pending id for /new when none exists", () => {
  const nextState = resolveThreadChatRouteSync({
    committedPathname: "/workspace/chats/new",
    threadIdFromPath: "new",
    currentThreadId: "previous-thread",
    newThreadId: null,
    createThreadId: () => "fresh-pending-thread",
  });

  expect(nextState).toEqual({
    threadId: "fresh-pending-thread",
    isNewThread: true,
    newThreadId: "fresh-pending-thread",
  });
});

test("useThreadChat route sync reuses the current pending id while staying on /new", () => {
  const nextState = resolveThreadChatRouteSync({
    committedPathname: "/workspace/chats/new",
    threadIdFromPath: "new",
    currentThreadId: "previous-thread",
    newThreadId: "current-pending-thread",
    createThreadId: () => "unexpected-new-uuid",
  });

  expect(nextState.threadId).toBe("current-pending-thread");
  expect(nextState.isNewThread).toBe(true);
  expect(nextState.newThreadId).toBe("current-pending-thread");
});

test("useThreadChat route sync moves history and runtime identity off /new after creation", () => {
  const nextState = resolveThreadChatRouteSync({
    committedPathname: "/workspace/chats/created-thread",
    threadIdFromPath: "new",
    currentThreadId: "pending-display-thread",
    newThreadId: "pending-display-thread",
    createThreadId: () => "unexpected-new-uuid",
  });

  expect(nextState).toEqual({
    threadId: "created-thread",
    isNewThread: false,
    newThreadId: null,
  });
  expect(threadRunsQueryKey(nextState.threadId)).toEqual([
    "thread",
    "created-thread",
    "runs",
  ]);
  expect(threadRuntimeSnapshotQueryKey(nextState.threadId)).toEqual([
    "thread",
    "created-thread",
    "runtime-snapshot",
  ]);
});
