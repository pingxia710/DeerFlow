import { expect, test } from "@rstest/core";

import { shouldClearPromptInputForThreadChange } from "@/components/workspace/input-box-state";

test("shouldClearPromptInputForThreadChange only clears on chat-thread switches", () => {
  expect(shouldClearPromptInputForThreadChange("thread-a", "thread-a")).toBe(
    false,
  );
  expect(shouldClearPromptInputForThreadChange("thread-a", "thread-b")).toBe(
    true,
  );
});
