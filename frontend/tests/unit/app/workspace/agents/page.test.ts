import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@rstest/core";

test("agent new chat runtime key includes the pending display thread id", () => {
  const source = readFileSync(
    resolve(
      __dirname,
      "../../../../../src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx",
    ),
    "utf-8",
  );

  expect(source).toContain("getAgentChatRuntimeKey(");
  expect(source).toContain("`agent-new-chat:${agentName}:${threadId}`");
  expect(source).toContain("resetThreadRuntimeSlot(runtimeKey)");
});
