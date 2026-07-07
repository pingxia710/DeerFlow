import { expect, test } from "@rstest/core";

import { shouldDeselectArtifactForThreadChange } from "@/components/workspace/chats/chat-box-state";

test("artifact selection is cleared only when the visible thread changes", () => {
  expect(shouldDeselectArtifactForThreadChange("thread-a", "thread-a")).toBe(
    false,
  );
  expect(shouldDeselectArtifactForThreadChange("thread-a", "thread-b")).toBe(
    true,
  );
});
