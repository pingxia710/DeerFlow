import { expect, test } from "@rstest/core";

import { resolveThreadChatRouteSync } from "@/components/workspace/chats/use-thread-chat";

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
