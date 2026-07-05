import { expect, test } from "@rstest/core";

import { shouldShowWelcomeMode } from "@/components/workspace/chats/welcome-mode";

const EMPTY_NEW_CHAT = {
  committedPathname: "/workspace/chats/new",
  hasMessages: false,
  hasPendingUsageMessages: false,
  isHistoryLoading: false,
  isNewThread: true,
  isStreamingOrLoading: false,
  pendingStartThreadId: null,
};

test("normal chat welcome mode does not return after send when the committed URL is a real thread", () => {
  expect(
    shouldShowWelcomeMode({
      ...EMPTY_NEW_CHAT,
      committedPathname: "/workspace/chats/created-thread",
      pendingStartThreadId: "created-thread",
    }),
  ).toBe(false);
});

test("normal chat welcome mode does not return while messages, streaming, or history loading exist", () => {
  expect(
    shouldShowWelcomeMode({
      ...EMPTY_NEW_CHAT,
      hasMessages: true,
    }),
  ).toBe(false);
  expect(
    shouldShowWelcomeMode({
      ...EMPTY_NEW_CHAT,
      isStreamingOrLoading: true,
    }),
  ).toBe(false);
  expect(
    shouldShowWelcomeMode({
      ...EMPTY_NEW_CHAT,
      isHistoryLoading: true,
    }),
  ).toBe(false);
});

test("agent chat welcome mode uses the same guarded /new-only rule", () => {
  expect(
    shouldShowWelcomeMode({
      ...EMPTY_NEW_CHAT,
      committedPathname: "/workspace/agents/command-room/chats/new",
    }),
  ).toBe(true);
  expect(
    shouldShowWelcomeMode({
      ...EMPTY_NEW_CHAT,
      committedPathname: "/workspace/agents/command-room/chats/agent-thread",
      hasPendingUsageMessages: true,
    }),
  ).toBe(false);
});

test("welcome mode leaves existing threads out of welcome when isNewThread is false", () => {
  expect(
    shouldShowWelcomeMode({
      ...EMPTY_NEW_CHAT,
      isNewThread: false,
    }),
  ).toBe(false);
});
