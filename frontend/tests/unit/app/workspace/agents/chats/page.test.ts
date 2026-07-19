import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@rstest/core";

import { getAgentChatRuntimeKey } from "@/app/workspace/agents/[agent_name]/chats/[thread_id]/page";
import { resolveAgentDisplayName } from "@/components/workspace/agent-welcome";

test("the command-room compatibility id is displayed as NextOS", () => {
  expect(resolveAgentDisplayName("command-room", "command-room")).toBe(
    "NextOS",
  );
  expect(resolveAgentDisplayName("researcher", "Research Team")).toBe(
    "Research Team",
  );
});

test("new agent chat runtime key is isolated by draft thread id", () => {
  const firstDraftKey = getAgentChatRuntimeKey("researcher", "draft-a", true);
  const secondDraftKey = getAgentChatRuntimeKey("researcher", "draft-b", true);

  expect(firstDraftKey).toBe("agent-new-chat:researcher:draft-a");
  expect(secondDraftKey).toBe("agent-new-chat:researcher:draft-b");
  expect(firstDraftKey).not.toBe(secondDraftKey);
});

test("saved agent chat runtime key is scoped by agent route owner", () => {
  expect(
    getAgentChatRuntimeKey("researcher", "backend-thread-123", false),
  ).toBe("agent-chat:researcher:backend-thread-123");
});

test("agent chat returns the send promise so a failed submit keeps the draft", () => {
  const source = readFileSync(
    resolve(
      process.cwd(),
      "src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx",
    ),
    "utf-8",
  );

  expect(source).not.toContain("void sendPromise");
  expect(source).toContain("return sendMessage(threadId, message);");
});
