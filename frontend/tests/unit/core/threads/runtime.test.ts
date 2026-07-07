import { expect, test } from "@rstest/core";

import {
  getThreadRuntimeSlotKeys,
  normalizeThreadRuntimeKey,
  resolveThreadRuntimeSlotId,
  shouldCollectThreadRuntimeSlot,
  shouldResetThreadRuntimeSlot,
} from "@/core/threads/runtime";

test("normalizeThreadRuntimeKey trims blank runtime keys", () => {
  expect(normalizeThreadRuntimeKey(" thread-a ")).toBe("thread-a");
  expect(normalizeThreadRuntimeKey("   ")).toBeNull();
  expect(normalizeThreadRuntimeKey(null)).toBeNull();
  expect(normalizeThreadRuntimeKey(undefined)).toBeNull();
});

test("getThreadRuntimeSlotKeys keeps runtime, backend, and display aliases unique", () => {
  expect(
    getThreadRuntimeSlotKeys({
      runtimeKey: "pending-a",
      threadId: "thread-a",
      displayThreadId: "pending-a",
    }),
  ).toEqual(["runtime:pending-a", "thread:thread-a", "display:pending-a"]);
});

test("getThreadRuntimeSlotKeys scopes equal raw aliases by key type", () => {
  expect(
    getThreadRuntimeSlotKeys({
      runtimeKey: "same-id",
      threadId: "same-id",
      displayThreadId: "same-id",
    }),
  ).toEqual(["runtime:same-id", "thread:same-id", "display:same-id"]);
});

test("resolveThreadRuntimeSlotId reuses pending slot after backend thread id arrives", () => {
  const aliases = new Map<string, string>([["display:pending-a", "slot-1"]]);

  expect(
    resolveThreadRuntimeSlotId(
      aliases,
      "thread:thread-a",
      undefined,
      "display:pending-a",
    ),
  ).toBe("slot-1");

  aliases.set("thread:thread-a", "slot-1");
  expect(resolveThreadRuntimeSlotId(aliases, "thread:thread-a")).toBe("slot-1");
});

test("resolveThreadRuntimeSlotId migrates /new display owner to the created thread slot", () => {
  const aliases = new Map<string, string>();
  const slotId = "slot-created";

  for (const key of getThreadRuntimeSlotKeys({
    runtimeKey: "chat:new:pending-1",
    displayThreadId: "pending-1",
  })) {
    aliases.set(key, slotId);
  }

  expect(
    resolveThreadRuntimeSlotId(
      aliases,
      "thread:created-thread",
      "display:pending-1",
    ),
  ).toBe(slotId);

  aliases.set("thread:created-thread", slotId);

  expect(resolveThreadRuntimeSlotId(aliases, "thread:created-thread")).toBe(
    slotId,
  );
  expect(new Set(aliases.values())).toEqual(new Set([slotId]));
});

test("shouldCollectThreadRuntimeSlot collects only idle unsubscribed slots", () => {
  expect(
    shouldCollectThreadRuntimeSlot({
      subscribers: 0,
      pendingInvocationCount: 0,
    }),
  ).toBe(true);

  expect(
    shouldCollectThreadRuntimeSlot({
      subscribers: 1,
      pendingInvocationCount: 0,
    }),
  ).toBe(false);
  expect(
    shouldCollectThreadRuntimeSlot({
      subscribers: 0,
      pendingInvocationCount: 1,
    }),
  ).toBe(false);
  expect(
    shouldCollectThreadRuntimeSlot({
      subscribers: 0,
      pendingInvocationCount: 0,
      isLoading: true,
    }),
  ).toBe(false);
  expect(
    shouldCollectThreadRuntimeSlot({
      subscribers: 0,
      pendingInvocationCount: 0,
      isUploading: true,
    }),
  ).toBe(false);
  expect(
    shouldCollectThreadRuntimeSlot({
      subscribers: 0,
      pendingInvocationCount: 0,
      recoveryState: "repairing",
    }),
  ).toBe(false);
});

test("shouldResetThreadRuntimeSlot resets only idle fixed slots", () => {
  expect(
    shouldResetThreadRuntimeSlot({
      subscribers: 0,
      pendingInvocationCount: 0,
    }),
  ).toBe(true);

  expect(
    shouldResetThreadRuntimeSlot({
      subscribers: 0,
      pendingInvocationCount: 0,
      isLoading: true,
    }),
  ).toBe(false);
  expect(
    shouldResetThreadRuntimeSlot({
      subscribers: 0,
      pendingInvocationCount: 0,
      isUploading: true,
    }),
  ).toBe(false);
  expect(
    shouldResetThreadRuntimeSlot({
      subscribers: 0,
      pendingInvocationCount: 1,
    }),
  ).toBe(false);
});

test("command-room-like runtime slot aliases stay isolated across pending and backend ids", () => {
  const aliases = new Map<string, string>();
  const slotA = "slot-a";
  const slotB = "slot-b";
  for (const key of getThreadRuntimeSlotKeys({
    runtimeKey: "command-room:pending:1",
    threadId: undefined,
    displayThreadId: "command-room:pending:1",
  })) {
    aliases.set(key, slotA);
  }
  for (const key of getThreadRuntimeSlotKeys({
    runtimeKey: "command-room:pending:2",
    threadId: "thread-b",
    displayThreadId: "command-room:pending:2",
  })) {
    aliases.set(key, slotB);
  }

  expect(slotA).not.toBe(slotB);
  expect(resolveThreadRuntimeSlotId(aliases, "thread:thread-b")).toBe(slotB);
  expect(
    resolveThreadRuntimeSlotId(aliases, "display:command-room:pending:1"),
  ).toBe(slotA);
});
