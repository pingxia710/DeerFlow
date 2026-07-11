import { afterEach, expect, test } from "@rstest/core";

import {
  __threadRuntimeTestUtils,
  clearThreadRuntime,
  getThreadRuntimeSlotKeys,
  normalizeThreadRuntimeKey,
  resolveThreadRuntimeSlotId,
  shouldCollectThreadRuntimeSlot,
  shouldResetThreadRuntimeSlot,
} from "@/core/threads/runtime";

const chatContext = { mode: "ultra" as const };
const agentContext = { mode: "ultra" as const, agent_name: "alpha" };

afterEach(() => {
  __threadRuntimeTestUtils.reset();
});

test("normalizeThreadRuntimeKey trims blank runtime keys", () => {
  expect(normalizeThreadRuntimeKey(" thread-a ")).toBe("thread-a");
  expect(normalizeThreadRuntimeKey("   ")).toBeNull();
  expect(normalizeThreadRuntimeKey(null)).toBeNull();
  expect(normalizeThreadRuntimeKey(undefined)).toBeNull();
});

test("getThreadRuntimeSlotKeys keeps runtime, backend, and display aliases unique", () => {
  expect(
    getThreadRuntimeSlotKeys({
      runtimeScope: "chat",
      runtimeKey: "pending-a",
      threadId: "thread-a",
      displayThreadId: "pending-a",
    }),
  ).toEqual([
    "runtime:pending-a",
    "thread:chat:thread-a",
    "display:chat:pending-a",
  ]);
});

test("getThreadRuntimeSlotKeys scopes equal raw aliases by key type", () => {
  expect(
    getThreadRuntimeSlotKeys({
      runtimeScope: "chat",
      runtimeKey: "same-id",
      threadId: "same-id",
      displayThreadId: "same-id",
    }),
  ).toEqual(["runtime:same-id", "thread:chat:same-id", "display:chat:same-id"]);
});

test("resolveThreadRuntimeSlotId reuses pending slot after backend thread id arrives", () => {
  const aliases = new Map<string, string>([
    ["display:chat:pending-a", "slot-1"],
  ]);

  expect(
    resolveThreadRuntimeSlotId(
      aliases,
      "thread:chat:thread-a",
      undefined,
      "display:chat:pending-a",
    ),
  ).toBe("slot-1");

  aliases.set("thread:chat:thread-a", "slot-1");
  expect(resolveThreadRuntimeSlotId(aliases, "thread:chat:thread-a")).toBe(
    "slot-1",
  );
});

test("resolveThreadRuntimeSlotId migrates /new display owner to the created thread slot", () => {
  const aliases = new Map<string, string>();
  const slotId = "slot-created";

  for (const key of getThreadRuntimeSlotKeys({
    runtimeScope: "chat",
    runtimeKey: "chat:new:pending-1",
    displayThreadId: "pending-1",
  })) {
    aliases.set(key, slotId);
  }

  expect(
    resolveThreadRuntimeSlotId(
      aliases,
      "thread:chat:created-thread",
      "display:chat:pending-1",
    ),
  ).toBe(slotId);

  aliases.set("thread:chat:created-thread", slotId);

  expect(
    resolveThreadRuntimeSlotId(aliases, "thread:chat:created-thread"),
  ).toBe(slotId);
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
    runtimeScope: "chat",
    runtimeKey: "command-room:pending:1",
    threadId: undefined,
    displayThreadId: "command-room:pending:1",
  })) {
    aliases.set(key, slotA);
  }
  for (const key of getThreadRuntimeSlotKeys({
    runtimeScope: "agent:command-room",
    runtimeKey: "command-room:pending:2",
    threadId: "thread-b",
    displayThreadId: "command-room:pending:2",
  })) {
    aliases.set(key, slotB);
  }

  expect(slotA).not.toBe(slotB);
  expect(
    resolveThreadRuntimeSlotId(aliases, "thread:agent:command-room:thread-b"),
  ).toBe(slotB);
  expect(
    resolveThreadRuntimeSlotId(aliases, "display:chat:command-room:pending:1"),
  ).toBe(slotA);
});

test("normal saved route and agent saved route with the same thread id use different slots", () => {
  const normalSend = () => undefined;
  const agentSend = () => undefined;
  const normal = __threadRuntimeTestUtils.register({
    runtimeScope: "chat",
    runtimeKey: "chat:thread-x",
    threadId: "thread-x",
    displayThreadId: "thread-x",
    context: chatContext,
    onSend: normalSend,
  });
  const normalSnapshot = { owner: "chat" };
  __threadRuntimeTestUtils.setSnapshot(normal.slotId, normalSnapshot);

  const agent = __threadRuntimeTestUtils.register({
    runtimeScope: "agent:alpha",
    runtimeKey: "agent-chat:alpha:thread-x",
    threadId: "thread-x",
    displayThreadId: "thread-x",
    context: agentContext,
    onSend: agentSend,
  });
  const agentSnapshot = { owner: "agent" };
  __threadRuntimeTestUtils.setSnapshot(agent.slotId, agentSnapshot);

  const normalEntry = __threadRuntimeTestUtils.get(normal.slotId);
  const agentEntry = __threadRuntimeTestUtils.get(agent.slotId);
  expect(normal.slotId).not.toBe(agent.slotId);
  expect(normalEntry?.context).toBe(chatContext);
  expect(agentEntry?.context).toBe(agentContext);
  expect(normalEntry?.callbacks.onSend).toBe(normalSend);
  expect(agentEntry?.callbacks.onSend).toBe(agentSend);
  expect(normalEntry?.snapshot).toBe(normalSnapshot);
  expect(agentEntry?.snapshot).toBe(agentSnapshot);
});

test("different agent names with the same thread id use different slots", () => {
  const alpha = __threadRuntimeTestUtils.register({
    runtimeScope: "agent:alpha",
    runtimeKey: "agent-chat:alpha:thread-x",
    threadId: "thread-x",
    displayThreadId: "thread-x",
    context: { ...agentContext, agent_name: "alpha" },
  });
  const beta = __threadRuntimeTestUtils.register({
    runtimeScope: "agent:beta",
    runtimeKey: "agent-chat:beta:thread-x",
    threadId: "thread-x",
    displayThreadId: "thread-x",
    context: { ...agentContext, agent_name: "beta" },
  });

  expect(alpha.slotId).not.toBe(beta.slotId);
  expect(__threadRuntimeTestUtils.get(alpha.slotId)?.keys).toContain(
    "thread:agent:alpha:thread-x",
  );
  expect(__threadRuntimeTestUtils.get(beta.slotId)?.keys).toContain(
    "thread:agent:beta:thread-x",
  );
});

test("same route owner and same thread id reuse the runtime slot", () => {
  const firstChat = __threadRuntimeTestUtils.register({
    runtimeScope: "chat",
    runtimeKey: "chat:thread-x",
    threadId: "thread-x",
    displayThreadId: "thread-x",
    context: chatContext,
  });
  const secondChat = __threadRuntimeTestUtils.register({
    runtimeScope: "chat",
    runtimeKey: "chat:thread-x",
    threadId: "thread-x",
    displayThreadId: "thread-x",
    context: chatContext,
  });
  const firstAgent = __threadRuntimeTestUtils.register({
    runtimeScope: "agent:alpha",
    runtimeKey: "agent-chat:alpha:thread-x",
    threadId: "thread-x",
    displayThreadId: "thread-x",
    context: agentContext,
  });
  const secondAgent = __threadRuntimeTestUtils.register({
    runtimeScope: "agent:alpha",
    runtimeKey: "agent-chat:alpha:thread-x",
    threadId: "thread-x",
    displayThreadId: "thread-x",
    context: agentContext,
  });

  expect(secondChat.slotId).toBe(firstChat.slotId);
  expect(secondAgent.slotId).toBe(firstAgent.slotId);
});

test("/new pending slots claim backend-created thread ids within the same owner scope", () => {
  const pendingChat = __threadRuntimeTestUtils.register({
    runtimeScope: "chat",
    runtimeKey: "new-chat:pending-chat",
    displayThreadId: "pending-chat",
    context: chatContext,
  });
  __threadRuntimeTestUtils.claim(pendingChat.slotId, "created-thread");
  const savedChat = __threadRuntimeTestUtils.register({
    runtimeScope: "chat",
    runtimeKey: "chat:created-thread",
    threadId: "created-thread",
    displayThreadId: "created-thread",
    context: chatContext,
  });

  const pendingAgent = __threadRuntimeTestUtils.register({
    runtimeScope: "agent:alpha",
    runtimeKey: "agent-new-chat:alpha:pending-agent",
    displayThreadId: "pending-agent",
    context: agentContext,
  });
  __threadRuntimeTestUtils.claim(pendingAgent.slotId, "created-thread");
  const savedAgent = __threadRuntimeTestUtils.register({
    runtimeScope: "agent:alpha",
    runtimeKey: "agent-chat:alpha:created-thread",
    threadId: "created-thread",
    displayThreadId: "created-thread",
    context: agentContext,
  });

  expect(savedChat.slotId).toBe(pendingChat.slotId);
  expect(savedAgent.slotId).toBe(pendingAgent.slotId);
  expect(savedChat.slotId).not.toBe(savedAgent.slotId);
});

test("claimRuntimeThreadId does not delete slots owned by other route scopes", () => {
  const normal = __threadRuntimeTestUtils.register({
    runtimeScope: "chat",
    runtimeKey: "chat:thread-x",
    threadId: "thread-x",
    displayThreadId: "thread-x",
    context: chatContext,
  });
  const agent = __threadRuntimeTestUtils.register({
    runtimeScope: "agent:alpha",
    runtimeKey: "agent-chat:alpha:thread-x",
    threadId: "thread-x",
    displayThreadId: "thread-x",
    context: agentContext,
  });

  __threadRuntimeTestUtils.claim(normal.slotId, "thread-x");
  expect(__threadRuntimeTestUtils.get(agent.slotId)).not.toBeNull();

  __threadRuntimeTestUtils.claim(agent.slotId, "thread-x");
  expect(__threadRuntimeTestUtils.get(normal.slotId)).not.toBeNull();
});

test("clearThreadRuntime deletes saved slots across scopes without deleting pending slots", () => {
  const normal = __threadRuntimeTestUtils.register({
    runtimeScope: "chat",
    runtimeKey: "chat:thread-x",
    threadId: "thread-x",
    displayThreadId: "thread-x",
    context: chatContext,
  });
  const agent = __threadRuntimeTestUtils.register({
    runtimeScope: "agent:alpha",
    runtimeKey: "agent-chat:alpha:thread-x",
    threadId: "thread-x",
    displayThreadId: "thread-x",
    context: agentContext,
  });

  clearThreadRuntime("thread-x");

  expect(__threadRuntimeTestUtils.get(normal.slotId)).toBeNull();
  expect(__threadRuntimeTestUtils.get(agent.slotId)).toBeNull();

  const pending = __threadRuntimeTestUtils.register({
    runtimeScope: "chat",
    runtimeKey: "new-chat:pending-thread",
    displayThreadId: "pending-thread",
    context: chatContext,
  });

  clearThreadRuntime("thread-x");

  expect(__threadRuntimeTestUtils.get(pending.slotId)).not.toBeNull();
});

test("workspace teardown clears saved and pending runtime slots", async () => {
  const saved = __threadRuntimeTestUtils.register({
    runtimeScope: "chat",
    runtimeKey: "chat:thread-x",
    threadId: "thread-x",
    displayThreadId: "thread-x",
    context: chatContext,
  });
  const pending = __threadRuntimeTestUtils.register({
    runtimeScope: "chat",
    runtimeKey: "new-chat:pending-thread",
    displayThreadId: "pending-thread",
    context: chatContext,
  });
  const runtimeModule = (await import("@/core/threads/runtime")) as Record<
    string,
    unknown
  >;
  const clearAllThreadRuntimes = runtimeModule.clearAllThreadRuntimes;

  expect(typeof clearAllThreadRuntimes).toBe("function");
  if (typeof clearAllThreadRuntimes !== "function") return;

  clearAllThreadRuntimes();

  expect(__threadRuntimeTestUtils.get(saved.slotId)).toBeNull();
  expect(__threadRuntimeTestUtils.get(pending.slotId)).toBeNull();
});
