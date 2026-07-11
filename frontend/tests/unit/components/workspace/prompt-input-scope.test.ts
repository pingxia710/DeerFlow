import { expect, test } from "@rstest/core";

import { shouldResetPromptInputScope } from "@/components/workspace/prompt-input-scope";

test("prompt input scope resets only when the composer owner changes", () => {
  expect(shouldResetPromptInputScope(null, "composer-a")).toBe(false);
  expect(shouldResetPromptInputScope("composer-a", "composer-a")).toBe(false);
  expect(shouldResetPromptInputScope("composer-a", "composer-b")).toBe(true);
});
