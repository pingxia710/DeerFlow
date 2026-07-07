import { expect, test } from "@rstest/core";

import { getAgentChatRuntimeKey } from "@/app/workspace/agents/[agent_name]/chats/[thread_id]/page";

test("new agent chat runtime key is isolated by draft thread id", () => {
  const firstDraftKey = getAgentChatRuntimeKey("researcher", "draft-a", true);
  const secondDraftKey = getAgentChatRuntimeKey("researcher", "draft-b", true);

  expect(firstDraftKey).toBe("agent-new-chat:researcher:draft-a");
  expect(secondDraftKey).toBe("agent-new-chat:researcher:draft-b");
  expect(firstDraftKey).not.toBe(secondDraftKey);
});

test("existing agent chat runtime key equals backend thread id", () => {
  expect(
    getAgentChatRuntimeKey("researcher", "backend-thread-123", false),
  ).toBe("backend-thread-123");
});
