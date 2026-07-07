import { expect, test } from "@rstest/core";

import {
  getThreadRuntimeSlotKeys,
  normalizeThreadRuntimeKey,
  resolveThreadRuntimeSlotId,
  shouldCollectThreadRuntimeSlot,
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
