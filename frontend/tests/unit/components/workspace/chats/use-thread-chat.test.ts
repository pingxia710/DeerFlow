import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@rstest/core";

import {
  clearThreadChatNavigationIntent,
  isThreadFinishForVisibleChat,
  markThreadChatNavigationIntent,
  pendingNavigationAllowsThreadStart,
  resolveThreadChatRouteSync,
  threadIdFromPendingNavigationIntent,
} from "@/components/workspace/chats/use-thread-chat";
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

test("completion ownership falls back to the committed URL during new-thread state sync", () => {
  expect(
    isThreadFinishForVisibleChat({
      finishThreadId: "created-thread",
      visibleThreadId: "stale-pending-thread",
      committedPathname: "/workspace/chats/created-thread",
    }),
  ).toBe(true);
  expect(
    isThreadFinishForVisibleChat({
      finishThreadId: "agent-thread",
      visibleThreadId: "stale-pending-thread",
      committedPathname: "/workspace/agents/command-room/chats/agent-thread",
    }),
  ).toBe(true);
});

test("completion ownership rejects background or metadata-less finishes", () => {
  expect(
    isThreadFinishForVisibleChat({
      finishThreadId: "background-thread",
      visibleThreadId: "visible-thread",
      committedPathname: "/workspace/chats/visible-thread",
    }),
  ).toBe(false);
  expect(
    isThreadFinishForVisibleChat({
      finishThreadId: null,
      visibleThreadId: "visible-thread",
      committedPathname: "/workspace/chats/visible-thread",
    }),
  ).toBe(false);
});

test("sidebar navigation intent remains visible until its route commits", () => {
  markThreadChatNavigationIntent("/workspace/chats/thread-b");
  expect(threadIdFromPendingNavigationIntent()).toBe("thread-b");
  expect(pendingNavigationAllowsThreadStart("thread-a")).toBe(false);
  expect(pendingNavigationAllowsThreadStart("thread-b")).toBe(true);

  clearThreadChatNavigationIntent("/workspace/chats/thread-a");
  expect(threadIdFromPendingNavigationIntent()).toBe("thread-b");

  clearThreadChatNavigationIntent("/workspace/chats/thread-b");
  expect(threadIdFromPendingNavigationIntent()).toBeNull();
  expect(pendingNavigationAllowsThreadStart("thread-a")).toBe(true);
});

test("new-chat navigation intent rejects a late start from the previous chat", () => {
  markThreadChatNavigationIntent("/workspace/chats/new");
  expect(threadIdFromPendingNavigationIntent()).toBeNull();
  expect(pendingNavigationAllowsThreadStart("previous-thread")).toBe(false);
  clearThreadChatNavigationIntent("/workspace/chats/new");
});

test("sidebar links record intent only for current-tab Next navigation", () => {
  for (const path of [
    "src/components/workspace/recent-chat-list.tsx",
    "src/components/workspace/workspace-header.tsx",
  ]) {
    const source = readFileSync(resolve(process.cwd(), path), "utf-8");

    expect(source).toContain("onNavigate=");
    expect(source).not.toMatch(
      /onClick=\{[\s\S]{0,180}markThreadChatNavigationIntent/,
    );
  }
});

test("workspace teardown clears pending chat navigation intent", async () => {
  const chatModule =
    (await import("@/components/workspace/chats/use-thread-chat")) as Record<
      string,
      unknown
    >;
  const resetThreadChatNavigationIntent =
    chatModule.resetThreadChatNavigationIntent;

  markThreadChatNavigationIntent("/workspace/chats/private-thread");
  expect(typeof resetThreadChatNavigationIntent).toBe("function");
  if (typeof resetThreadChatNavigationIntent !== "function") return;

  resetThreadChatNavigationIntent();

  expect(threadIdFromPendingNavigationIntent()).toBeNull();
});
