import { expect, test } from "@rstest/core";

import {
  getPromptInputComposerKey,
  getFollowupSuggestionsErrorAction,
  shouldApplyPromptInputSubmitContinuation,
  shouldClearPromptInputForThreadChange,
} from "@/components/workspace/input-box-state";
import { UnauthorizedError } from "@/core/api/fetcher";

test("shouldClearPromptInputForThreadChange only clears on chat-thread switches", () => {
  expect(shouldClearPromptInputForThreadChange("thread-a", "thread-a")).toBe(
    false,
  );
  expect(shouldClearPromptInputForThreadChange("thread-a", "thread-b")).toBe(
    true,
  );
});

test("prompt input composer key separates thread and runtime session", () => {
  expect(
    getPromptInputComposerKey({
      threadId: "thread-a",
      composerSessionId: "runtime-a",
    }),
  ).not.toBe(
    getPromptInputComposerKey({
      threadId: "thread-b",
      composerSessionId: "runtime-a",
    }),
  );
  expect(
    getPromptInputComposerKey({
      threadId: "thread-a",
      composerSessionId: "runtime-a",
    }),
  ).not.toBe(
    getPromptInputComposerKey({
      threadId: "thread-a",
      composerSessionId: "runtime-b",
    }),
  );
});

test("prompt input submit continuation applies only to the captured composer", () => {
  const oldComposer = getPromptInputComposerKey({
    threadId: "thread-a",
    composerSessionId: "runtime-a",
  });
  const nextComposer = getPromptInputComposerKey({
    threadId: "thread-b",
    composerSessionId: "runtime-b",
  });

  expect(
    shouldApplyPromptInputSubmitContinuation(oldComposer, oldComposer),
  ).toBe(true);
  expect(
    shouldApplyPromptInputSubmitContinuation(nextComposer, oldComposer),
  ).toBe(false);
});

test("follow-up suggestions refresh auth on unauthorized errors", () => {
  expect(getFollowupSuggestionsErrorAction(new UnauthorizedError())).toBe(
    "refresh-auth",
  );
  expect(getFollowupSuggestionsErrorAction({ status: 401 })).toBe(
    "refresh-auth",
  );
});

test("follow-up suggestions clear optional UI on non-auth errors", () => {
  expect(getFollowupSuggestionsErrorAction(new Error("Gateway timeout"))).toBe(
    "clear",
  );
});

test("failed queued drafts restore only into their owning empty composer", async () => {
  const state = await import("@/components/workspace/input-box-state");
  const getAction = Reflect.get(state, "getFailedQueuedMessageDraftAction") as
    | ((input: {
        failedThreadId: string;
        currentThreadId: string;
        failedText: string;
        currentText: string;
      }) => "restore" | "acknowledge" | "wait")
    | undefined;

  expect(typeof getAction).toBe("function");
  if (!getAction) return;

  expect(
    getAction({
      failedThreadId: "thread-a",
      currentThreadId: "thread-a",
      failedText: "retry me",
      currentText: "",
    }),
  ).toBe("restore");
  expect(
    getAction({
      failedThreadId: "thread-a",
      currentThreadId: "thread-a",
      failedText: "retry me",
      currentText: "retry me",
    }),
  ).toBe("acknowledge");
  expect(
    getAction({
      failedThreadId: "thread-a",
      currentThreadId: "thread-b",
      failedText: "retry me",
      currentText: "",
    }),
  ).toBe("wait");
});
