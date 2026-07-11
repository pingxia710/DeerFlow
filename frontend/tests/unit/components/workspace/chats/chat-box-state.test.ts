import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@rstest/core";

import {
  getCurrentThreadArtifacts,
  shouldDeselectArtifactForThreadChange,
} from "@/components/workspace/chats/chat-box-state";

test("artifact selection is cleared only when the visible thread changes", () => {
  expect(shouldDeselectArtifactForThreadChange("thread-a", "thread-a")).toBe(
    false,
  );
  expect(shouldDeselectArtifactForThreadChange("thread-a", "thread-b")).toBe(
    true,
  );
});

test("current thread artifacts include write-file tool calls from its messages", () => {
  const artifacts = getCurrentThreadArtifacts(
    ["/outputs/final.md"],
    [
      {
        id: "message-1",
        type: "ai",
        tool_calls: [
          {
            id: "tool-1",
            name: "write_file",
            args: { path: "/outputs/draft.md" },
          },
          {
            id: "tool-2",
            name: "read_file",
            args: { path: "/outputs/ignored.md" },
          },
        ],
      },
    ],
  );

  expect(artifacts).toEqual([
    "/outputs/final.md",
    "write-file:/outputs/draft.md?message_id=message-1&tool_call_id=tool-1",
  ]);
});

test("current thread artifacts deduplicate persisted and transient entries", () => {
  const transient =
    "write-file:/outputs/draft.md?message_id=message-1&tool_call_id=tool-1";

  expect(
    getCurrentThreadArtifacts(
      [transient],
      [
        {
          id: "message-1",
          type: "ai",
          tool_calls: [
            {
              id: "tool-1",
              name: "str_replace",
              args: { path: "/outputs/draft.md" },
            },
          ],
        },
      ],
    ),
  ).toEqual([transient]);
});

test("artifact list renders the same derived artifacts as the panel state", () => {
  const source = readFileSync(
    resolve(process.cwd(), "src/components/workspace/chats/chat-box.tsx"),
    "utf-8",
  );

  expect(source).toMatch(/<ArtifactFileList[\s\S]*?files=\{currentArtifacts\}/);
});
