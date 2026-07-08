import { expect, test } from "@rstest/core";

import { getChatRuntimeKey } from "@/app/workspace/chats/[thread_id]/page";

test("new conversation runtime key is scoped by draft thread id", () => {
  expect(getChatRuntimeKey("draft-a", true)).toBe("new-chat:draft-a");
  expect(getChatRuntimeKey("draft-b", true)).toBe("new-chat:draft-b");
  expect(getChatRuntimeKey("draft-a", true)).not.toBe(
    getChatRuntimeKey("draft-b", true),
  );
});

test("saved normal conversation runtime key is scoped by chat route", () => {
  expect(getChatRuntimeKey("thread-1", false)).toBe("chat:thread-1");
});
