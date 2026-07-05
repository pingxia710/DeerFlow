import { expect, test } from "@rstest/core";

import {
  getFollowupSuggestionsErrorAction,
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
